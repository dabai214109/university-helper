import { CARD, formatTaskTime } from '../utils'

// Statuses that still represent live work; removing one is refused by the
// backend, so the UI disables the button rather than letting the request 409.
const ACTIVE_STATUSES = new Set([
  'running',
  'pending',
  'paused',
  'cancelling',
  'scheduled',
  'recurring',
  // A queued entry is still pending work: the backend refuses to hide it and
  // its only meaningful action is to cancel it.
  'queued',
])

const isActive = (status) => ACTIVE_STATUSES.has(String(status || '').toLowerCase())

const STATUS_LABELS = {
  running: '进行中',
  pending: '排队中',
  queued: '排队等待中',
  paused: '已暂停',
  cancelling: '取消中',
  scheduled: '定时中',
  recurring: '周期任务',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

function TaskRow({ task, selected, onRestore, onRemove, removing }) {
  const status = String(task.status || '').toLowerCase()
  const active = isActive(status)
  const queued = status === 'queued'
  const label = STATUS_LABELS[status] || task.status || '未知'

  return (
    <li
      className={`rounded-xl border px-3 py-2.5 transition-colors duration-200 ${
        selected ? 'border-primary bg-primary/10' : 'border-border bg-surface'
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-sm text-text" title={task.task_id}>
            {task.task_id}
          </p>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text/70">
            <span>状态：{label}</span>
            <span>更新时间：{formatTaskTime(task.updated_at)}</span>
          </div>
        </div>

        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            aria-label={`恢复任务 ${task.task_id}`}
            aria-pressed={selected}
            onClick={() => onRestore(task.task_id)}
            disabled={queued}
            title={queued ? '排队中：前一个任务结束后会自动开始，要取消请直接停止任务' : undefined}
            className={`min-h-[36px] rounded-lg border px-3 text-xs font-medium transition-colors ${
              queued
                ? 'cursor-not-allowed border-border bg-surface text-text-muted'
                : 'cursor-pointer'
            } ${
              !queued && selected
                ? 'border-primary bg-primary text-white'
                : !queued
                  ? 'border-border bg-surface text-text/80 hover:border-primary/40 hover:bg-surface-hover'
                  : ''
            }`}
          >
            恢复
          </button>
          <button
            type="button"
            aria-label={`移除任务 ${task.task_id}`}
            onClick={() => onRemove(task)}
            disabled={active || removing}
            title={active ? '任务仍在运行，请先停止再移除' : '从队列移除（记录保留）'}
            className="min-h-[36px] cursor-pointer rounded-lg border border-danger/30 bg-danger-surface px-3 text-xs font-medium text-danger transition-colors hover:bg-danger/10 disabled:cursor-not-allowed disabled:border-border disabled:bg-surface disabled:text-text-muted"
          >
            {removing ? '移除中...' : '移除'}
          </button>
        </div>
      </div>
    </li>
  )
}

export default function TaskHistorySection({
  taskHistory,
  taskId,
  selectTaskFromHistory,
  removeTaskFromHistory,
  removingTaskId = '',
}) {
  return (
    <section className={CARD}>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xl font-semibold text-text">任务队列</h2>
        {taskHistory.length > 0 && (
          <span className="text-xs text-text-muted">共 {taskHistory.length} 条</span>
        )}
      </div>

      {taskHistory.length === 0 ? (
        <p className="text-sm text-text-muted">暂无任务记录</p>
      ) : (
        <ul className="space-y-2">
          {taskHistory.map((task) => (
            <TaskRow
              key={task.task_id}
              task={task}
              selected={task.task_id === taskId}
              onRestore={selectTaskFromHistory}
              onRemove={removeTaskFromHistory}
              removing={removingTaskId === task.task_id}
            />
          ))}
        </ul>
      )}

      <p className="mt-3 text-xs text-text-muted">
        「移除」只是从队列隐藏，任务记录仍保留在服务器上；运行中的任务需先停止。
      </p>
    </section>
  )
}
