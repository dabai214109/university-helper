import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, History, RefreshCw } from 'lucide-react'

import { api } from '../../utils/api'
import { Card, EmptyState, StatusBadge, useToast } from '../../components'

const BTN_GHOST =
  'inline-flex min-h-[38px] items-center justify-center gap-1.5 rounded-xl border border-border bg-surface px-4 text-sm font-medium text-text/70 hover:bg-surface-hover disabled:opacity-50'
const INPUT = 'w-full rounded-xl border border-border bg-surface px-3 py-2 text-sm text-text'

const PAGE_SIZE = 20

// Status filters offered to the administrator. The values are the backend's
// task statuses; the Chinese labels live here so the API stays language-neutral.
const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'running', label: '进行中' },
  { value: 'pending', label: '排队中' },
  { value: 'paused', label: '已暂停' },
  { value: 'scheduled', label: '定时中' },
  { value: 'recurring', label: '周期任务' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]

const formatTime = (value) => {
  if (!value) return '--'
  const ms = new Date(value).getTime()
  if (!Number.isFinite(ms)) return String(value)
  return new Date(ms).toLocaleString('zh-CN', { hour12: false })
}

// "3/10 章" when the backend reported a chapter total, else the course tally.
const progressText = (record) => {
  const progress = record?.progress || {}
  const done = Number(progress.completed_chapters) || 0
  const total = Number(progress.total_chapters) || 0
  if (total > 0) return `${done}/${total} 章`
  const coursesDone = Number(progress.completed) || 0
  const coursesTotal = Number(progress.total) || 0
  if (coursesTotal > 0) return `${coursesDone}/${coursesTotal} 门`
  return '--'
}

function CourseNames({ names }) {
  if (!Array.isArray(names) || names.length === 0) {
    return <span className="text-text-muted">--</span>
  }
  const shown = names.slice(0, 2)
  const rest = names.length - shown.length
  return (
    <div className="flex flex-wrap items-center gap-1">
      {shown.map((name) => (
        <span
          key={name}
          className="inline-block max-w-[12rem] truncate rounded-md bg-surface-hover px-2 py-0.5 text-xs text-text/80"
        >
          {name}
        </span>
      ))}
      {rest > 0 && <span className="text-xs text-text-muted">+{rest}</span>}
    </div>
  )
}

/**
 * Per-user learning history for the admin console.
 *
 * Reads `/admin/learning-records`, which projects only the fields this table
 * renders — never the full task payload (that carries up to 1000 log lines).
 */
export default function LearningRecordsPanel({ users = [] }) {
  const toast = useToast()
  const [records, setRecords] = useState([])
  const [pagination, setPagination] = useState({ page: 1, page_size: PAGE_SIZE, total: 0, total_pages: 1 })
  const [loading, setLoading] = useState(true)
  const [forbidden, setForbidden] = useState(false)

  const [userFilter, setUserFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [page, setPage] = useState(1)

  const loadRecords = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (userFilter) params.set('user_id', userFilter)
      if (statusFilter) params.set('status', statusFilter)
      // The date inputs are local dates; widen them to full days so the range
      // reads the way a user expects ("from 1st to 3rd" includes the 3rd).
      if (since) params.set('since', new Date(`${since}T00:00:00`).toISOString())
      if (until) params.set('until', new Date(`${until}T23:59:59.999`).toISOString())
      params.set('page', String(page))
      params.set('page_size', String(PAGE_SIZE))

      const resp = await api(`/admin/learning-records?${params.toString()}`)
      if (resp?.status === 'success') {
        setRecords(Array.isArray(resp.data) ? resp.data : [])
        if (resp.pagination) setPagination(resp.pagination)
      }
    } catch (err) {
      if (err?.status === 403) {
        setForbidden(true)
        return
      }
      toast.error(err?.message || '加载刷课记录失败')
    } finally {
      setLoading(false)
    }
  }, [userFilter, statusFilter, since, until, page, toast])

  useEffect(() => {
    void loadRecords()
  }, [loadRecords])

  // Any filter change resets to the first page: staying on page 5 of a
  // different result set would show an empty table.
  const updateFilter = (setter) => (event) => {
    setter(event.target.value)
    setPage(1)
  }

  const userOptions = useMemo(
    () => (users || []).map((user) => ({
      value: String(user.user_id ?? user.id ?? ''),
      label: user.username || user.email || String(user.user_id ?? user.id ?? ''),
    })).filter((option) => option.value),
    [users],
  )

  const resetFilters = () => {
    setUserFilter('')
    setStatusFilter('')
    setSince('')
    setUntil('')
    setPage(1)
  }

  if (forbidden) {
    return (
      <Card padding="compact">
        <EmptyState title="需要管理员权限" hint="你当前不是管理员，无法查看刷课记录。" />
      </Card>
    )
  }

  const { page: currentPage = 1, total = 0, total_pages: totalPages = 1 } = pagination || {}
  const hasFilters = Boolean(userFilter || statusFilter || since || until)

  return (
    <Card padding="compact">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-text">
            <History className="h-5 w-5 text-primary" aria-hidden="true" />
            用户刷课记录
          </h2>
          <p className="mt-1 text-xs text-text-muted">
            共 {total} 条记录，按最后更新时间倒序。含已完成与失败的历史任务。
          </p>
        </div>
        <button type="button" className={BTN_GHOST} onClick={() => { void loadRecords() }} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          刷新
        </button>
      </div>

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-text/70">用户</span>
          <select
            className={INPUT}
            value={userFilter}
            onChange={updateFilter(setUserFilter)}
            aria-label="按用户筛选"
          >
            <option value="">全部用户</option>
            {userOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-medium text-text/70">状态</span>
          <select
            className={INPUT}
            value={statusFilter}
            onChange={updateFilter(setStatusFilter)}
            aria-label="按状态筛选"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value || 'all'} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-medium text-text/70">开始日期</span>
          <input
            type="date"
            className={INPUT}
            value={since}
            onChange={updateFilter(setSince)}
            aria-label="起始日期"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-medium text-text/70">结束日期</span>
          <input
            type="date"
            className={INPUT}
            value={until}
            onChange={updateFilter(setUntil)}
            aria-label="结束日期"
          />
        </label>
      </div>

      {hasFilters && (
        <button type="button" className={`${BTN_GHOST} mb-3`} onClick={resetFilters}>
          清除筛选
        </button>
      )}

      {loading && records.length === 0 ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-12 animate-pulse rounded-xl bg-surface-hover" />
          ))}
        </div>
      ) : records.length === 0 ? (
        <EmptyState
          icon={History}
          title={hasFilters ? '没有符合条件的记录' : '暂无刷课记录'}
          hint={
            hasFilters
              ? '试着放宽筛选条件或清除筛选。'
              : '用户在学习通泛雅页发起刷课任务后，记录会出现在这里。'
          }
        />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border text-xs uppercase tracking-wider text-text-muted">
                <tr>
                  <th className="px-3 py-3">用户</th>
                  <th className="px-3 py-3">课程</th>
                  <th className="px-3 py-3">状态</th>
                  <th className="px-3 py-3">进度</th>
                  <th className="px-3 py-3">开始时间</th>
                  <th className="px-3 py-3">结束时间</th>
                  <th className="px-3 py-3">最后更新</th>
                </tr>
              </thead>
              <tbody>
                {records.map((record) => (
                  <tr
                    key={record.task_id}
                    className="border-b border-border/50 hover:bg-surface-hover/50"
                  >
                    <td className="px-3 py-3 font-medium text-text">
                      {record.username || record.user_id || '--'}
                    </td>
                    <td className="px-3 py-3">
                      <CourseNames names={record.course_names} />
                    </td>
                    <td className="px-3 py-3">
                      <StatusBadge status={record.status} />
                    </td>
                    <td className="px-3 py-3 font-mono text-xs text-text/80">
                      {progressText(record)}
                    </td>
                    <td className="px-3 py-3 text-xs text-text/70">{formatTime(record.started_at)}</td>
                    <td className="px-3 py-3 text-xs text-text/70">
                      {record.finished_at ? (
                        formatTime(record.finished_at)
                      ) : (
                        // Deliberately not "进行中" — the status column already
                        // says that, and repeating it reads as a second status.
                        <span className="text-text-muted">未结束</span>
                      )}
                    </td>
                    <td className="px-3 py-3 text-xs text-text/70">{formatTime(record.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between gap-3">
              <span className="text-xs text-text-muted">
                第 {currentPage} / {totalPages} 页
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  className={BTN_GHOST}
                  onClick={() => setPage((prev) => Math.max(1, prev - 1))}
                  disabled={currentPage <= 1 || loading}
                  aria-label="上一页"
                >
                  <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                  上一页
                </button>
                <button
                  type="button"
                  className={BTN_GHOST}
                  onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
                  disabled={currentPage >= totalPages || loading}
                  aria-label="下一页"
                >
                  下一页
                  <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </Card>
  )
}
