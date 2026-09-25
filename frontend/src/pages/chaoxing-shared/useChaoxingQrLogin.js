import { useCallback, useEffect, useRef, useState } from 'react'

// Chaoxing's QR handshake is server-driven: the backend owns the Chaoxing
// session and the browser only polls for state, mirroring the Zhihuishu flow.
export const CHAOXING_QR_POLL_INTERVAL_MS = 2000

// States the backend reports for a QR session. `scanned` is the intermediate
// "confirm on your phone" step; treating it as terminal would strand the user.
export const CHAOXING_QR_TERMINAL_STATES = new Set(['success', 'failed'])

/**
 * Drive a Chaoxing QR login session.
 *
 * @param request  `(path, options) => Promise<payload>` where `path` is
 *                 relative to the Chaoxing API root (`/qr-login`,
 *                 `/login-status`, `/logout`). Each page adapts its own
 *                 request helper, because they use different HTTP clients.
 * @param onSuccess Called once, after the session is confirmed and the
 *                 backend has a usable Chaoxing session.
 */
export default function useChaoxingQrLogin({ request, onSuccess }) {
  const [qrCode, setQrCode] = useState('')
  const [qrStatus, setQrStatus] = useState('idle')
  const [qrMessage, setQrMessage] = useState('')
  const [qrError, setQrError] = useState('')
  const [qrLoading, setQrLoading] = useState(false)
  const [loggedIn, setLoggedIn] = useState(false)
  const [nickname, setNickname] = useState('')

  const pollRef = useRef(null)
  const generationRef = useRef(0)
  const sessionOwnerRef = useRef({ generation: 0, sessionId: '' })
  const pollsInFlightRef = useRef(new Set())
  const requestRef = useRef(request)
  const onSuccessRef = useRef(onSuccess)

  // Keeping the callbacks in refs means the polling effect never has to be
  // torn down and rebuilt just because a parent re-rendered with a new closure.
  requestRef.current = request
  onSuccessRef.current = onSuccess

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const ownsSession = useCallback((generation, sessionId) => {
    const owner = sessionOwnerRef.current
    return (
      generationRef.current === generation &&
      owner.generation === generation &&
      owner.sessionId === sessionId
    )
  }, [])

  const invalidateGeneration = useCallback(() => {
    const generation = generationRef.current + 1
    generationRef.current = generation
    sessionOwnerRef.current = { generation, sessionId: '' }
    pollsInFlightRef.current.clear()
    stopPolling()
    return generation
  }, [stopPolling])

  useEffect(() => () => {
    generationRef.current += 1
    sessionOwnerRef.current = { generation: generationRef.current, sessionId: '' }
    stopPolling()
  }, [stopPolling])

  const refreshLoginStatus = useCallback(async () => {
    try {
      const resp = await requestRef.current('/chaoxing/login-status', { method: 'GET' })
      const data = resp?.data || {}
      setLoggedIn(Boolean(data.logged_in))
      setNickname(String(data.nickname || ''))
      return Boolean(data.logged_in)
    } catch {
      // A status probe is advisory: a failure must not surface as a page error.
      return false
    }
  }, [])

  const pollQrStatus = useCallback(
    async (sessionId, generation) => {
      if (!ownsSession(generation, sessionId)) return
      if (pollsInFlightRef.current.has(generation)) return
      pollsInFlightRef.current.add(generation)

      try {
        const resp = await requestRef.current(`/chaoxing/qr-login/${sessionId}`, { method: 'GET' })
        if (!ownsSession(generation, sessionId)) return

        const data = resp?.data || {}
        const nextStatus = String(data.status || 'pending')
        setQrStatus(nextStatus)
        setQrMessage(String(data.message || ''))
        if (data.qr_code) {
          // The backend re-renders the code when the original expires, so a
          // slow scan can still succeed.
          setQrCode((prev) => (prev === data.qr_code ? prev : data.qr_code))
        }

        if (nextStatus === 'success') {
          stopPolling()
          setLoggedIn(true)
          setQrLoading(false)
          if (typeof onSuccessRef.current === 'function') {
            await onSuccessRef.current()
          }
        } else if (nextStatus === 'failed') {
          stopPolling()
          setQrLoading(false)
          setQrError(String(data.message || '扫码登录失败'))
        }
      } catch (err) {
        if (!ownsSession(generation, sessionId)) return
        stopPolling()
        setQrLoading(false)
        setQrStatus('failed')
        setQrError(err?.message || '查询二维码状态失败。')
      } finally {
        pollsInFlightRef.current.delete(generation)
      }
    },
    [ownsSession, stopPolling]
  )

  const startQrLogin = useCallback(async () => {
    const generation = invalidateGeneration()
    let sessionId = ''
    setQrLoading(true)
    setQrError('')
    setQrStatus('loading')
    setQrMessage('正在生成二维码...')
    setQrCode('')

    try {
      const resp = await requestRef.current('/chaoxing/qr-login', { method: 'POST' })
      if (generationRef.current !== generation) return

      const data = resp?.data || {}
      sessionId = String(data.session_id || '')
      if (!sessionId || !data.qr_code) {
        throw new Error('二维码登录响应无效。')
      }

      sessionOwnerRef.current = { generation, sessionId }
      setQrCode(data.qr_code)
      setQrStatus(String(data.status || 'pending'))
      setQrMessage(String(data.message || '请使用学习通 App 扫描二维码'))

      stopPolling()
      pollRef.current = setInterval(() => {
        void pollQrStatus(sessionId, generation)
      }, CHAOXING_QR_POLL_INTERVAL_MS)
    } catch (err) {
      if (generationRef.current !== generation) return
      setQrStatus('failed')
      setQrError(err?.message || '创建二维码会话失败。')
      setQrMessage('')
      setQrLoading(false)
    }
  }, [invalidateGeneration, pollQrStatus, stopPolling])

  const cancelQrLogin = useCallback(async () => {
    const owner = sessionOwnerRef.current
    const sessionId = owner.sessionId
    invalidateGeneration()
    setQrCode('')
    setQrStatus('idle')
    setQrMessage('')
    setQrError('')
    setQrLoading(false)
    if (!sessionId) return
    try {
      await requestRef.current(`/chaoxing/qr-login/${sessionId}/cancel`, { method: 'POST' })
    } catch {
      // The server expires abandoned sessions on its own; a failed cancel is
      // not worth surfacing to the user.
    }
  }, [invalidateGeneration])

  const logout = useCallback(async () => {
    invalidateGeneration()
    setQrCode('')
    setQrStatus('idle')
    setQrMessage('')
    setQrError('')
    setQrLoading(false)
    try {
      await requestRef.current('/chaoxing/logout', { method: 'POST' })
    } finally {
      setLoggedIn(false)
      setNickname('')
    }
  }, [invalidateGeneration])

  return {
    qrCode,
    qrStatus,
    qrMessage,
    qrError,
    qrLoading,
    loggedIn,
    setLoggedIn,
    nickname,
    startQrLogin,
    cancelQrLogin,
    logout,
    refreshLoginStatus,
    stopPolling,
  }
}
