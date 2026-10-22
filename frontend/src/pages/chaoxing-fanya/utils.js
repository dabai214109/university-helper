export const TOKEN_ERROR = /(invalid token|token has expired|token validation failed|missing token|invalid authentication credentials)/i


// 内置的言溪题库 Token。
//
// 只在**发请求时**使用，不写进组件的 state，因此它不会出现在输入框、
// DOM 或任何可见文本里 —— 用户看到的是「使用默认 Token」这行提示。
// 用户在输入框里填了内容，就用用户自己的，忽略这个默认值。
export const DEFAULT_YANXI_TOKEN = 'c83091d7a10743479d24da8bec5e6195'


// 内置 Token 只属于言溪。选中别的题库时它既不生效、也不该被提示。
export const DEFAULT_TOKEN_PROVIDER = 'TikuYanxi'


/**
 * 选中的题库里是否有言溪 —— 决定「默认 Token」这套提示要不要出现。
 */
export const usesDefaultToken = (providers) => {
  const list = Array.isArray(providers) ? providers : [providers]
  return list.includes(DEFAULT_TOKEN_PROVIDER)
}


/**
 * 决定这次请求实际要带哪个 Token。
 *
 * 空字符串表示"用默认"，不是"没有 token" —— 这个区别很重要：言溪缺 Token
 * 会被后端从回退链摘掉，默认值兜底才能让开箱即用的承诺成立。
 *
 * 默认值**只在选中言溪时**才填：内置 Token 是言溪账号的额度，拿去喂 Like
 * 题库只会浪费额度且必然认证失败。所以没选言溪时，空就是真的空。
 *
 * `useDefault=false` 是"我就是要不带 token"的出口。
 */
export const resolveTikuToken = (typed, providers = [DEFAULT_TOKEN_PROVIDER], useDefault = true) => {
  const text = String(typed ?? '').trim()
  if (text) return text
  if (!useDefault) return ''
  return usesDefaultToken(providers) ? DEFAULT_YANXI_TOKEN : ''
}


export const POLL_MS = 2500


export const DONE_STATUSES = new Set(['completed', 'failed', 'error', 'cancelled'])


// `recurring` is a long-lived template, so it belongs in the restore set (the
// page should re-attach to it) and NOT in DONE_STATUSES.
export const RESTORE_STATUSES = new Set(['running', 'pending', 'paused', 'scheduled', 'recurring'])


export const CARD = 'rounded-2xl border border-border/20 bg-surface/80 p-6 shadow-lg backdrop-blur-lg'


export const toNum = (value, fallback = 0) => {


  const n = Number(value)


  return Number.isFinite(n) ? n : fallback


}


export const toTimestamp = (value) => {


  const ts = new Date(value).getTime()


  return Number.isFinite(ts) ? ts : 0


}


export const normalizeCourseText = (value) => {


  if (value === undefined || value === null) return ''


  return String(value).trim()


}


export const getCourseId = (course) => {


  const courseId = normalizeCourseText(course?.courseId ?? course?.course_id)


  const classId = normalizeCourseText(course?.classId ?? course?.clazzId ?? course?.class_id ?? course?.clazz_id)


  const cpi = normalizeCourseText(course?.cpi)


  if (courseId && classId) {


    return cpi ? `${courseId}_${classId}_${cpi}` : `${courseId}_${classId}`


  }


  const explicitId = normalizeCourseText(course?.id)


  return explicitId || courseId || classId


}


export const getCourseName = (course) => {
  const name = [
    course?.name,
    course?.courseName,
    course?.title,
    course?.course_name,
    course?.courseTitle,
    course?.course_title,
    course?.className,
    course?.clazzName,
    course?.label,
  ]
    .map(normalizeCourseText)
    .find(Boolean)
  const selector = getCourseId(course)
  return name || (selector ? `课程 ${selector}` : '未命名课程')
}


// Chapters come from /course/chapters/{id}; the backend already reports
// `has_finished` / `need_unlock` per point, so these only normalize the shape.
export const getChapterId = (chapter) => normalizeCourseText(chapter?.id)


export const getChapterTitle = (chapter) =>
  normalizeCourseText(chapter?.title) || normalizeCourseText(chapter?.name) || '未命名章节'


export const normalizeTaskItem = (task) => {


  if (!task || typeof task !== 'object') return null


  const taskId = String(task.task_id || task.taskId || task.id || '').trim()


  if (!taskId) return null


  const status = String(task.status || task.task_status || task.state || 'unknown').toLowerCase()


  const updatedAt =


    task.updated_at ||


    task.updatedAt ||


    task.update_time ||


    task.last_update ||


    task.started_at ||


    task.start_time ||


    task.created_at ||


    new Date().toISOString()


  return { ...task, task_id: taskId, status, updated_at: updatedAt }


}


export const mergeTaskHistory = (prev, incoming, prepend = false) => {


  const incomingList = (Array.isArray(incoming) ? incoming : [incoming]).map(normalizeTaskItem).filter(Boolean)


  if (incomingList.length === 0) return prev


  const incomingMap = new Map(incomingList.map((item) => [item.task_id, item]))


  const seen = new Set()


  const merged = []


  const push = (item) => {


    if (!item?.task_id || seen.has(item.task_id)) return


    seen.add(item.task_id)


    merged.push(item)


  }


  if (prepend) {


    incomingList.forEach(push)


    prev.forEach((item) => push(incomingMap.get(item.task_id) || item))


    return merged


  }


  prev.forEach((item) => push(incomingMap.get(item.task_id) || item))


  incomingList.forEach(push)


  return merged


}


export const formatTaskTime = (value) => {


  if (!value) return '--'


  const date = new Date(value)


  if (Number.isNaN(date.getTime())) return String(value)


  return date.toLocaleString('zh-CN', { hour12: false })


}
