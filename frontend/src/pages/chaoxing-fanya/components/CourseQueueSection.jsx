import { CARD, getCourseId, getCourseName } from '../utils'


/**
 * 已确认队列的清单。
 *
 * 单独成卡，夹在「学习通课程」和「播放速度」之间 —— 用户的动作顺序是：
 * 在课程列表勾选 → 确认加入队列 → 看这一块知道自己排了什么 → 往下调参数。
 * 之前它嵌在课程列表卡片里，视线要来回跳。
 */
export default function CourseQueueSection({ courses, queuedCourses = [], onClearQueue }) {
  if (queuedCourses.length === 0) return null

  const nameOf = (courseId) =>
    getCourseName(courses.find((course) => getCourseId(course) === courseId) || {}) || courseId

  return (
    <section className={CARD}>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-xl font-semibold text-text">已加入队列（按执行顺序）</h2>
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm text-text-muted">{queuedCourses.length} 门</span>
          {onClearQueue && (
            <button
              type="button"
              onClick={onClearQueue}
              className="cursor-pointer rounded-lg border border-border bg-surface px-3 py-1.5 text-sm text-text/70 transition-colors hover:bg-surface-hover"
            >
              清空队列
            </button>
          )}
        </div>
      </div>

      <ol className="space-y-2">
        {queuedCourses.map((courseId, index) => (
          <li
            key={courseId}
            className="flex items-center gap-3 rounded-xl border border-border/30 bg-surface/60 px-4 py-2.5 text-sm"
          >
            <span className="inline-grid h-6 w-6 shrink-0 place-items-center rounded-full bg-primary/15 font-mono text-xs font-semibold text-primary">
              {index + 1}
            </span>
            <span className="min-w-0 flex-1 truncate text-text">{nameOf(courseId)}</span>
            <span className="hidden shrink-0 font-mono text-[11px] text-text-muted sm:inline">
              {courseId}
            </span>
          </li>
        ))}
      </ol>

      <p className="mt-3 text-xs text-text-muted">
        点击「开始刷课」后，会按上面顺序逐门执行；如果当前已有任务在跑，会被排到它后面自动开始。
      </p>
    </section>
  )
}
