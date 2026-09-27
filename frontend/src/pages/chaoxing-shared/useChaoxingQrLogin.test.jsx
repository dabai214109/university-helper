import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'

import useChaoxingQrLogin, { CHAOXING_QR_POLL_INTERVAL_MS } from './useChaoxingQrLogin'

// The backend answers `image/jpeg`; the panel must not hardcode png.
const QR_JPEG = 'ZmFrZS1qcGVn'

const sessionResponse = (overrides = {}) => ({
  data: {
    session_id: 'session-1',
    status: 'pending',
    message: '请使用学习通 App 扫描二维码',
    qr_code: QR_JPEG,
    qr_mime: 'image/jpeg',
    ...overrides,
  },
})

describe('useChaoxingQrLogin', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  test('clears the loading flag once the code is on screen', async () => {
    // Regression: the session-creation path never reset `qrLoading`, so the
    // button stayed on "生成中..." and the panel — which only rendered the
    // image while `qrLoading` was false — hid the QR code entirely.
    const request = vi.fn().mockResolvedValue(sessionResponse())

    const { result } = renderHook(() => useChaoxingQrLogin({ request }))

    await act(async () => {
      await result.current.startQrLogin()
    })

    expect(result.current.qrCode).toBe(QR_JPEG)
    expect(result.current.qrStatus).toBe('pending')
    expect(result.current.qrLoading).toBe(false)
  })

  test('exposes the media type reported by the backend', async () => {
    const request = vi.fn().mockResolvedValue(sessionResponse({ qr_mime: 'image/png' }))

    const { result } = renderHook(() => useChaoxingQrLogin({ request }))

    await act(async () => {
      await result.current.startQrLogin()
    })

    expect(result.current.qrMime).toBe('image/png')
  })

  test('polls until the scan is confirmed and then stops', async () => {
    vi.useFakeTimers()
    const request = vi.fn()
      .mockResolvedValueOnce(sessionResponse())
      .mockResolvedValueOnce(sessionResponse({ status: 'scanned', message: '已扫码，请在手机上确认登录' }))
      .mockResolvedValueOnce(sessionResponse({ status: 'success', message: '登录成功' }))

    const onSuccess = vi.fn()
    const { result } = renderHook(() => useChaoxingQrLogin({ request, onSuccess }))

    await act(async () => {
      await result.current.startQrLogin()
    })

    // Two poll ticks: scanned, then success.
    for (let tick = 0; tick < 2; tick += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(CHAOXING_QR_POLL_INTERVAL_MS)
      })
    }

    expect(result.current.qrStatus).toBe('success')
    expect(result.current.loggedIn).toBe(true)
    expect(result.current.qrLoading).toBe(false)
    expect(onSuccess).toHaveBeenCalledTimes(1)
  })

  test('surfaces a failed scan as an error and stops loading', async () => {
    vi.useFakeTimers()
    const request = vi.fn()
      .mockResolvedValueOnce(sessionResponse())
      .mockResolvedValueOnce(sessionResponse({ status: 'failed', message: '已在手机端取消登录' }))

    const { result } = renderHook(() => useChaoxingQrLogin({ request }))

    await act(async () => {
      await result.current.startQrLogin()
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CHAOXING_QR_POLL_INTERVAL_MS)
    })

    expect(result.current.qrStatus).toBe('failed')
    expect(result.current.qrError).toBe('已在手机端取消登录')
    expect(result.current.qrLoading).toBe(false)
  })

  test('reports a rejected session request instead of hanging on loading', async () => {
    const request = vi.fn().mockRejectedValue(new Error('network down'))

    const { result } = renderHook(() => useChaoxingQrLogin({ request }))

    await act(async () => {
      await result.current.startQrLogin()
    })

    await waitFor(() => expect(result.current.qrStatus).toBe('failed'))
    expect(result.current.qrLoading).toBe(false)
    expect(result.current.qrError).toBe('network down')
  })

  test('rejects a response that carries no session or image', async () => {
    const request = vi.fn().mockResolvedValue({ data: { session_id: '', qr_code: '' } })

    const { result } = renderHook(() => useChaoxingQrLogin({ request }))

    await act(async () => {
      await result.current.startQrLogin()
    })

    expect(result.current.qrStatus).toBe('failed')
    expect(result.current.qrLoading).toBe(false)
    expect(result.current.qrError).toBe('二维码登录响应无效。')
  })

  test('cancel returns the panel to idle and clears the code', async () => {
    const request = vi.fn().mockResolvedValue(sessionResponse())

    const { result } = renderHook(() => useChaoxingQrLogin({ request }))

    await act(async () => {
      await result.current.startQrLogin()
    })
    await act(async () => {
      await result.current.cancelQrLogin()
    })

    expect(result.current.qrStatus).toBe('idle')
    expect(result.current.qrCode).toBe('')
    expect(result.current.qrLoading).toBe(false)
    expect(request).toHaveBeenCalledWith('/chaoxing/qr-login/session-1/cancel', { method: 'POST' })
  })

  test('a status probe failure never throws at the caller', async () => {
    const request = vi.fn().mockRejectedValue(new Error('boom'))

    const { result } = renderHook(() => useChaoxingQrLogin({ request }))

    let loggedIn
    await act(async () => {
      loggedIn = await result.current.refreshLoginStatus()
    })

    expect(loggedIn).toBe(false)
    expect(result.current.loggedIn).toBe(false)
  })
})
