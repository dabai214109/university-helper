import { describe, expect, test } from 'vitest'

import {
  describeInvalidCourseIds,
  isValidCourseId,
  parseCourseIds,
  splitCourseIds,
} from './courseIds'

// The admin panel's course field takes `courseId_classId_cpi` IDs, not course
// names. Admins kept pasting names and getting an opaque "no courses matched",
// so these helpers split on any separator and reject non-ID input up front.

describe('splitCourseIds', () => {
  test('splits on an English comma', () => {
    expect(splitCourseIds('1_2_3,4_5_6')).toEqual(['1_2_3', '4_5_6'])
  })

  test('splits on a Chinese comma', () => {
    // This is the separator that used to break the whole field.
    expect(splitCourseIds('1_2_3，4_5_6')).toEqual(['1_2_3', '4_5_6'])
  })

  test('splits on semicolons and newlines', () => {
    expect(splitCourseIds('1_2_3;4_5_6\n7_8_9')).toEqual(['1_2_3', '4_5_6', '7_8_9'])
  })

  test('splits on spaces', () => {
    expect(splitCourseIds('1_2_3  4_5_6')).toEqual(['1_2_3', '4_5_6'])
  })

  test('trims and drops empty entries', () => {
    expect(splitCourseIds('  1_2_3 ,, 4_5_6  ')).toEqual(['1_2_3', '4_5_6'])
  })

  test('empty input yields no entries', () => {
    expect(splitCourseIds('')).toEqual([])
    expect(splitCourseIds(null)).toEqual([])
    expect(splitCourseIds(undefined)).toEqual([])
  })
})

describe('isValidCourseId', () => {
  test('accepts courseId_classId_cpi', () => {
    expect(isValidCourseId('264919356_152899105_411776100')).toBe(true)
  })

  test('accepts courseId_classId (cpi optional)', () => {
    expect(isValidCourseId('264919356_152899105')).toBe(true)
  })

  test('rejects a course name', () => {
    expect(isValidCourseId('形势与政策（2026秋）')).toBe(false)
  })

  test('rejects a name with its ID line appended', () => {
    expect(isValidCourseId('形势与政策（2026秋） 264919356_152899105_411776100')).toBe(false)
  })

  test('rejects a bare number', () => {
    expect(isValidCourseId('264919356')).toBe(false)
  })

  test('rejects an empty string', () => {
    expect(isValidCourseId('')).toBe(false)
    expect(isValidCourseId(null)).toBe(false)
  })
})

describe('parseCourseIds', () => {
  test('returns every valid ID in order', () => {
    const { valid, invalid } = parseCourseIds('1_2_3,4_5_6')

    expect(valid).toEqual(['1_2_3', '4_5_6'])
    expect(invalid).toEqual([])
  })

  test('separates the invalid entries while keeping the valid ones', () => {
    const { valid, invalid } = parseCourseIds('1_2_3，形势与政策（2026秋）')

    expect(valid).toEqual(['1_2_3'])
    expect(invalid).toEqual(['形势与政策（2026秋）'])
  })

  test('an empty field is valid and means "all courses"', () => {
    expect(parseCourseIds('')).toEqual({ valid: [], invalid: [] })
  })
})

describe('describeInvalidCourseIds', () => {
  test('quotes the offending text back so the mistake is obvious', () => {
    const message = describeInvalidCourseIds(['形势与政策（2026秋）'])

    expect(message).toContain('形势与政策（2026秋）')
    expect(message).toContain('课程 ID')
    expect(message).toContain('264919356_152899105_411776100')
  })

  test('caps the quoted examples but reports the full count', () => {
    const message = describeInvalidCourseIds(['A', 'B', 'C', 'D', 'E'])

    expect(message).toContain('A')
    expect(message).not.toContain('E')
    expect(message).toContain('5')
  })
})
