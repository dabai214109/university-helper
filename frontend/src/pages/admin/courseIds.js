// Parsing helpers for the course-ID field used by the admin "start task" panel.
//
// The backend's `course_ids` are `courseId_classId_cpi` strings, not course
// names. Admins kept pasting the course name (or its display line) and getting
// a bare "No available courses after filtering", which does not say why. These
// helpers split on any separator a user might type and validate the shape
// before the request is sent.

// Splits on English/Chinese commas, semicolons, and any whitespace. A user
// pasting a multi-line list from the course card must work too.
export const COURSE_ID_SEPARATOR = /[,，;；\s]+/

// courseId_classId_cpi, with cpi optional. Anchored so "形势与政策（2026秋）"
// fails instead of being sent to the backend where it can only mismatch.
const COURSE_ID_PATTERN = /^\d+_\d+(_\d+)?$/

export const splitCourseIds = (raw) =>
  String(raw || '')
    .split(COURSE_ID_SEPARATOR)
    .map((part) => part.trim())
    .filter(Boolean)

export const isValidCourseId = (value) => COURSE_ID_PATTERN.test(String(value || '').trim())

/**
 * Split and validate in one pass.
 *
 * @returns {{ valid: string[], invalid: string[] }}
 *   `invalid` keeps the user's raw text so the error can quote it back.
 */
export const parseCourseIds = (raw) => {
  const parts = splitCourseIds(raw)
  const valid = []
  const invalid = []
  for (const part of parts) {
    if (isValidCourseId(part)) valid.push(part)
    else invalid.push(part)
  }
  return { valid, invalid }
}

/** Build the message shown when some entries are not course IDs. */
export const describeInvalidCourseIds = (invalid) => {
  const shown = invalid.slice(0, 3).map((value) => `「${value}」`).join('、')
  const more = invalid.length > 3 ? ` 等 ${invalid.length} 项` : ''
  return (
    `${shown}${more} 不是课程 ID。\n\n` +
    '请填课程名称下方那串数字，格式为 courseId_classId_cpi，\n' +
    '例如：264919356_152899105_411776100\n\n' +
    '多门课程可用逗号、分号或换行分隔。'
  )
}
