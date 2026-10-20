import { describe, expect, test } from 'vitest'

import {
  DEFAULT_TOKEN_PROVIDER,
  DEFAULT_YANXI_TOKEN,
  resolveTikuToken,
  usesDefaultToken,
} from './utils'

describe('resolveTikuToken', () => {
  test('falls back to the built-in token when the field is empty', () => {
    // Empty means "use the default", NOT "no token" — 言溪 without a token gets
    // dropped from the fallback chain, which would silently disable answering.
    expect(resolveTikuToken('')).toBe(DEFAULT_YANXI_TOKEN)
  })

  test('treats whitespace as empty', () => {
    expect(resolveTikuToken('   ')).toBe(DEFAULT_YANXI_TOKEN)
    expect(resolveTikuToken(null)).toBe(DEFAULT_YANXI_TOKEN)
  })

  test('a user-typed token always wins', () => {
    expect(resolveTikuToken('mine')).toBe('mine')
    expect(resolveTikuToken('  mine  ')).toBe('mine')
  })

  test('opting out yields an empty token', () => {
    // The escape hatch for "I really do not want to send any token".
    expect(resolveTikuToken('', false)).toBe('')
    expect(resolveTikuToken('  ', false)).toBe('')
  })

  test('opting out does not discard an explicit token', () => {
    expect(resolveTikuToken('mine', false)).toBe('mine')
  })

  test('the built-in token looks like the 言溪 format', () => {
    expect(DEFAULT_YANXI_TOKEN).toMatch(/^[0-9a-f]{32}$/)
  })

  test('the default is only sent when 言溪 is among the providers', () => {
    // 内置 Token 是言溪账号的额度；喂给别家必然认证失败还白耗额度。
    expect(resolveTikuToken('', ['TikuYanxi'])).toBe(DEFAULT_YANXI_TOKEN)
    expect(resolveTikuToken('', ['TikuYanxi', 'TikuGo'])).toBe(DEFAULT_YANXI_TOKEN)
    expect(resolveTikuToken('', ['TikuGo'])).toBe('')
    expect(resolveTikuToken('', ['TikuLike'])).toBe('')
    expect(resolveTikuToken('', [])).toBe('')
  })

  test('a typed token is sent regardless of the selected providers', () => {
    expect(resolveTikuToken('mine', ['TikuLike'])).toBe('mine')
    expect(resolveTikuToken('mine', ['TikuYanxi'])).toBe('mine')
  })

  test('a single provider string is accepted, not just an array', () => {
    expect(resolveTikuToken('', 'TikuYanxi')).toBe(DEFAULT_YANXI_TOKEN)
    expect(resolveTikuToken('', 'TikuGo')).toBe('')
  })
})

describe('usesDefaultToken', () => {
  test('detects 言溪 in an array or a bare string', () => {
    expect(usesDefaultToken(['TikuYanxi'])).toBe(true)
    expect(usesDefaultToken(['TikuGo', 'TikuYanxi'])).toBe(true)
    expect(usesDefaultToken('TikuYanxi')).toBe(true)
    expect(usesDefaultToken(['TikuGo'])).toBe(false)
    expect(usesDefaultToken([])).toBe(false)
    expect(usesDefaultToken(null)).toBe(false)
  })

  test('the default provider constant is 言溪', () => {
    expect(DEFAULT_TOKEN_PROVIDER).toBe('TikuYanxi')
  })
})
