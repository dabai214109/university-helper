import { CheckCircle2, CircleDashed, Loader2, XCircle } from 'lucide-react'

import { toNum } from '../utils'

// Queue entry states reported by the backend in `progress.courses[].status`.
const ENTRY_META = {
  pending: { label: '等待中', Icon: CircleDashed, tone: 'text-text-muted' },
  running: { label: '进行中', Icon: Loader2, tone: 'text-primary' },
  completed: { label: '已完成', Icon: CheckCircle2, tone: 'text-success' },
  failed: { label: '失败', Icon: XCircle, tone: 'text-danger' },
  cancelled: { label: '已取消', Icon: XCircle, tone: 'text-text-muted' },
}

/**
 * Show the whole course queue in the order the user ticked the courses.
 *
 * The scalar counters elsewhere on this card only describe the course being
 * worked on right now; this list is what makes the FIFO order visible.
 */
export default function CourseQueue({ courses }) {
  if (!Array.isArray(courses) || courses.length === 0) return null

  const done = courses.filter((course) => course?.status === 'completed').length

  return (
    <div className="mt-4 rounded-xl border border-border bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium text-text/80">执行队列（按勾选顺序）</p>
        <span className="font-mono text-xs text-text-muted">
          {done}/{courses.length}
        </span>
      </div>

      <ol className="space-y-1.5">
        {courses.map((course, index) => {
          const status = String(course?.status || 'pending')
          const meta = ENTRY_META[status] || ENTRY_META.pending
          const { Icon } = meta
          const isRunning = status === 'running'
          const chaptersTotal = toNum(course?.chapters_total)
          const chaptersDone = toNum(course?.chapters_done)
          return (
            <li
              key={`${index}-${course?.name || ''}`}
              className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm ${
                isRunning ? 'bg-primary/10' : 'bg-surface-hover/50'
              }`}
            >
              <span className="w-5 shrink-0 text-center font-mono text-xs text-text-muted">
                {index + 1}
              </span>
              <Icon
                className={`h-4 w-4 shrink-0 ${meta.tone} ${isRunning ? 'animate-spin' : ''}`}
                aria-hidden="true"
              />
              <span className="min-w-0 flex-1 truncate text-text">{course?.name || '--'}</span>
              {chaptersTotal > 0 && (
                <span className="shrink-0 font-mono text-xs text-text-muted">
                  {chaptersDone}/{chaptersTotal} 章
                </span>
              )}
              <span className={`shrink-0 text-xs ${meta.tone}`}>{meta.label}</span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
