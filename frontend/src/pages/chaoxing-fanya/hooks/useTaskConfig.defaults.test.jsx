import { act, renderHook } from '@testing-library/react'
import { describe, expect, test } from 'vitest'

import useTaskConfig from './useTaskConfig'

describe('useTaskConfig defaults', () => {
  test('speed and concurrency start at 1', () => {
    const { result } = renderHook(() => useTaskConfig())

    // Conservative defaults: 1x playback and one chapter at a time are what a
    // new user expects, and they avoid tripping Chaoxing's rate limiting.
    expect(result.current.speed).toBe(1)
    expect(result.current.concurrency).toBe(1)
  })

  test('仅言溪题库 is selected by default', () => {
    const { result } = renderHook(() => useTaskConfig())
    expect(result.current.tikuProvider).toEqual(['TikuYanxi'])
  })

  test('the token starts empty; the default is applied at submit time', () => {
    const { result } = renderHook(() => useTaskConfig())
    // Deliberately NOT prefilled — the built-in token must stay out of state so
    // it cannot render into the input or the DOM.
    expect(result.current.tikuToken).toBe('')
    expect(result.current.tikuTokenTouched).toBe(false)
  })

  test('a user-typed token overrides the default', () => {
    const { result } = renderHook(() => useTaskConfig())
    act(() => result.current.setTikuToken('mine'))
    expect(result.current.tikuToken).toBe('mine')
  })

  test('AI and 硅基流动 keys stay empty by default', () => {
    const { result } = renderHook(() => useTaskConfig())
    expect(result.current.aiApiKey).toBe('')
    expect(result.current.siliconFlowKey).toBe('')
    expect(result.current.aiEndpoint).toBe('')
  })
})
