import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  Ban,
  CalendarClock,
  CheckCircle2,
  PlayCircle,
  RefreshCw,
  Server,
  Timer,
  Users,
  XCircle,
} from 'lucide-react'
import { api } from '../utils/api'
import {
  Card,
  EmptyState,
  StatusBadge,
  useToast,
} from '../components'

const CARD = 'rounded-2xl border border-border/20 bg-surface/80 p-6 shadow-lg backdrop-blur-lg'
const INPUT = 'w-full rounded-xl border border-border bg-surface px-4 py-2.5 text-sm text-text'
const LABEL = 'mb-1.5 block text-sm font-medium text-text/80'
const BTN_PRIMARY =
  'inline-flex min-h-[42px] items-center justify-center gap-2 rounded-xl bg-primary px-5 text-sm font-semibold text-white disabled:opacity-50'
const BTN_GHOST =
  'inline-flex min-h-[38px] items-center gap-1.5 rounded-xl border border-border bg-surface px-4 text-sm font-medium text-text/70 hover:bg-surface-hover disabled:opacity-50'

const SCHEDULED_STATUS = 'scheduled'
const ACTIVE_STATUSES = new Set(['running', 'pending', 'paused', 'cancelling', 'scheduled'])
const isLive = (status) => ACTIVE_STATUSES.has(String(status || '').toLowerCase())

const toMs = (value) => {
  if (!value) return 0
  const ms = new Date(value).getTime()
  return Number.isFinite(ms) ? ms : 0
}

const formatTime = (value) => {
  if (!value) return '--'
  const ms = toMs(value)
  if (!ms) return String(value)
  return new Date(ms).toLocaleString('zh-CN', { hour12: false })
}

const formatClock = (value) => {
  const ms = toMs(value)
  if (!ms) return '--:--:--'
  return new Date(ms).toLocaleTimeString('zh-CN', { hour12: false })
}

const LEVEL_TONE = {
  success: 'text-success',
  error: 'text-danger',
  warning: 'text-warning',
  info: 'text-primary',
}

function KPI({ label, value, accent, sub }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-5 space-y-1">
      <p className="text-xs font-semibold uppercase tracking-widest text-text-muted">{label}</p>
      <p className={`text-3xl font-bold ${accent || 'text-text'}`}>{value ?? '--'}</p>
      {sub && <p className="text-xs text-text-muted">{sub}</p>}
    </div>
  )
}

// Ticks locally once a second so the queue stays live without re-fetching.
function Countdown({ target }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const targetMs = toMs(target)
  if (!targetMs) return <span className="font-mono text-text-muted">--:--:--</span>

  const left = targetMs - now
  if (left <= 0) return <span className="font-mono font-semibold text-success">正在启动…</span>

  const total = Math.floor(left / 1000)
  const h = String(Math.floor(total / 3600)).padStart(2, '0')
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, '0')
  const s = String(total % 60).padStart(2, '0')
  return <span className="font-mono font-semibold tabular-nums text-warning">{h}:{m}:{s}</span>
}

function Jitter({ minutes }) {
  const n = Number(minutes) || 0
  if (n <= 0) return null
  return <span className="text-warning"> ±{n}min</span>
}

function ProgressBar({ progress }) {
  const total = Number(progress?.total) || 0
  const completed = Number(progress?.completed) || 0
  const pct = total > 0 ? Math.min(100, Math.max(0, (completed / total) * 100)) : 0
  return (
    <div className="flex min-w-[120px] items-center gap-2">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-hover">
        <div className="h-full rounded-full bg-success transition-all" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-9 text-right font-mono text-xs text-text-muted">{Math.round(pct)}%</span>
    </div>
  )
}

function ScheduledCard({ task, onStop, stopping }) {
  const schedule = task.schedule || {}
  const fireAt = schedule.fire_at
  // Prefer the resolved fire time; before the dispatcher runs, start_at is the
  // closest available estimate.
  const countdownTarget = fireAt || schedule.start_at
  const isResolved = Boolean(fireAt)

  return (
    <div className="relative overflow-hidden rounded-2xl border border-warning/30 bg-surface p-5 shadow-sm">
      <span className="absolute inset-y-0 left-0 w-1 bg-warning" aria-hidden="true" />

      <div className="mb-3 flex items-start justify-between gap-3">
        <span className="inline-flex items-center gap-1.5 rounded-lg bg-warning-surface px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-warning">
          <CalendarClock className="h-3.5 w-3.5" aria-hidden="true" />
          chaoxing
        </span>
        <StatusBadge status={SCHEDULED_STATUS} />
      </div>

      <h3 className="truncate font-mono text-sm font-semibold text-text" title={task.task_id}>
        {task.task_id}
      </h3>
      <p className="mt-1 truncate text-xs text-text-muted">
        执行用户：{task.username || task.user_id || '--'}
      </p>

      <dl className="mt-4 space-y-1.5 text-xs">
        <div className="flex justify-between gap-3">
          <dt className="text-text-muted">计划启动</dt>
          <dd className="text-right text-text/80">
            {formatTime(schedule.start_at)}
            <Jitter minutes={schedule.start_jitter_min} />
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-text-muted">计划停止</dt>
          <dd className="text-right text-text/80">
            {schedule.stop_at ? (
              <>
                {formatTime(schedule.stop_at)}
                <Jitter minutes={schedule.stop_jitter_min} />
              </>
            ) : (
              <span className="text-text-muted">不限（跑完为止）</span>
            )}
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-text-muted">实际启动</dt>
          <dd className="text-right text-text/80">
            {isResolved ? formatTime(fireAt) : <span className="text-text-muted">待解析</span>}
          </dd>
        </div>
      </dl>

      <div className="mt-4 flex items-center justify-between gap-3 border-t border-dashed border-border pt-3">
        <div>
          <p className="text-[11px] text-text-muted">
            {isResolved ? '距实际启动' : '距计划启动（抖动待解析）'}
          </p>
          <p className="mt-0.5 text-lg">
            <Countdown target={countdownTarget} />
          </p>
        </div>
        <button
          type="button"
          onClick={() => { void onStop(task.task_id) }}
          disabled={stopping}
          className="inline-flex min-h-[36px] items-center gap-1.5 rounded-xl border border-danger/30 bg-danger-surface px-3 text-xs font-semibold text-danger hover:bg-danger/10 disabled:opacity-50"
        >
          <Ban className="h-3.5 w-3.5" aria-hidden="true" />
          {stopping ? '取消中...' : '取消'}
        </button>
      </div>
    </div>
  )
}

function TaskTable({ tasks, onStop, stoppingId, emptyTitle }) {
  if (tasks.length === 0) return <EmptyState title={emptyTitle} />
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-border text-xs uppercase tracking-wider text-text-muted">
          <tr>
            <th className="px-3 py-3">任务 ID</th>
            <th className="px-3 py-3">用户</th>
            <th className="px-3 py-3">状态</th>
            <th className="px-3 py-3">进度</th>
            <th className="px-3 py-3">消息</th>
            <th className="px-3 py-3">更新时间</th>
            <th className="px-3 py-3">操作</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((task) => (
            <tr key={task.task_id} className="border-b border-border/50 hover:bg-surface-hover/50">
              <td className="px-3 py-3 font-mono text-xs text-text/70">{task.task_id}</td>
              <td className="px-3 py-3 font-medium text-text">{task.username || task.user_id || '--'}</td>
              <td className="px-3 py-3"><StatusBadge status={task.status} /></td>
              <td className="px-3 py-3">
                {task.status === SCHEDULED_STATUS
                  ? <span className="font-mono text-xs text-warning">定时中</span>
                  : <ProgressBar progress={task.progress} />}
              </td>
              <td className="max-w-xs truncate px-3 py-3 text-xs text-text/70">{task.message || '--'}</td>
              <td className="px-3 py-3 text-xs text-text/70">{formatTime(task.updated_at)}</td>
              <td className="px-3 py-3">
                {isLive(task.status) && (
                  <button
                    type="button"
                    onClick={() => { void onStop(task.task_id) }}
                    disabled={stoppingId === task.task_id}
                    className="rounded-lg border border-danger/30 bg-danger-surface px-3 py-1.5 text-xs font-semibold text-danger hover:bg-danger/10 disabled:opacity-50"
                  >
                    {stoppingId === task.task_id ? '停止中...' : '停止'}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function StartTaskPanel({ users, onStarted }) {
  const toast = useToast()
  const [form, setForm] = useState({
    user_id: '',
    username: '',
    password: '',
    course_ids: '',
    mode: 'now',
    start_at: '',
    stop_at: '',
    start_jitter_min: 10,
    stop_jitter_min: 15,
    speed: 1.5,
    concurrency: 4,
    unopened_strategy: 'retry',
  })
  const [submitting, setSubmitting] = useState(false)

  const set = (key) => (event) => {
    const value = event?.target ? event.target.value : event
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const submit = async () => {
    if (!form.user_id) {
      toast.error('请选择执行用户')
      return
    }
    if (!form.username.trim() || !form.password) {
      toast.error('请填写该用户的超星账号和密码')
      return
    }
    const scheduled = form.mode === 'scheduled'
    if (scheduled && !form.start_at) {
      toast.error('请选择启动时间')
      return
    }
    if (scheduled && form.stop_at) {
      if (new Date(form.stop_at).getTime() <= new Date(form.start_at).getTime()) {
        toast.error('停止时间必须晚于启动时间')
        return
      }
    }

    setSubmitting(true)
    try {
      const body = {
        user_id: form.user_id,
        username: form.username.trim(),
        password: form.password,
        course_ids: form.course_ids
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
        speed: Number(form.speed),
        concurrency: Number(form.concurrency),
        unopened_strategy: form.unopened_strategy,
      }
      if (scheduled) {
        body.start_at = new Date(form.start_at).toISOString()
        if (form.stop_at) body.stop_at = new Date(form.stop_at).toISOString()
        body.start_jitter_min = Number(form.start_jitter_min) || 0
        body.stop_jitter_min = Number(form.stop_jitter_min) || 0
      }

      const resp = await api('/admin/task/start', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      if (resp?.status === 'success') {
        toast.success(scheduled ? '已加入定时队列' : '任务已启动')
        setForm((prev) => ({ ...prev, password: '', course_ids: '' }))
        onStarted?.()
      }
    } catch (err) {
      toast.error(err?.message || '启动任务失败')
    } finally {
      setSubmitting(false)
    }
  }

  const selectedUser = users.find((u) => String(u.id) === String(form.user_id))

  return (
    <Card padding="compact">
      <h2 className="text-lg font-semibold text-text">发起任务</h2>
      <p className="mt-1 text-xs text-text-muted">
        以管理员身份为指定用户发起刷课任务。需要该用户的超星账号密码——应用没有共享凭据库，
        这些值会按普通任务一样加密落库。
      </p>

      <div className="mt-5 grid gap-5 md:grid-cols-2">
        <div>
          <label htmlFor="admin-start-user" className={LABEL}>执行用户 <span className="text-primary">*</span></label>
          <select id="admin-start-user" className={INPUT} value={form.user_id} onChange={set('user_id')}>
            <option value="">请选择用户</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.username || u.email || u.id}
                {u.tenant_db_name ? ` — ${u.tenant_db_name}` : ''}
              </option>
            ))}
          </select>
          {selectedUser && (
            <p className="mt-1 text-xs text-text-muted">
              当前活跃任务 {selectedUser.active_tasks ?? 0} · 定时任务 {selectedUser.scheduled_tasks ?? 0}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="admin-start-username" className={LABEL}>超星账号 <span className="text-primary">*</span></label>
          <input
            id="admin-start-username"
            className={INPUT}
            value={form.username}
            onChange={set('username')}
            placeholder="该用户的手机号 / 学号"
          />
        </div>

        <div>
          <label htmlFor="admin-start-password" className={LABEL}>超星密码 <span className="text-primary">*</span></label>
          <input
            id="admin-start-password"
            type="password"
            className={INPUT}
            value={form.password}
            onChange={set('password')}
            placeholder="仅用于本次任务，加密存储"
          />
        </div>

        <div>
          <label htmlFor="admin-start-courses" className={LABEL}>课程（可选）</label>
          <input
            id="admin-start-courses"
            className={INPUT}
            value={form.course_ids}
            onChange={set('course_ids')}
            placeholder="留空 = 全部课程；多门用逗号分隔"
          />
        </div>

        <div>
          <label htmlFor="admin-start-speed" className={LABEL}>播放倍速：{Number(form.speed).toFixed(1)}x</label>
          <input
            id="admin-start-speed"
            type="range" min="1" max="2" step="0.1"
            className="w-full"
            value={form.speed}
            onChange={set('speed')}
          />
        </div>

        <div>
          <label htmlFor="admin-start-concurrency" className={LABEL}>并发章节：{form.concurrency}</label>
          <input
            id="admin-start-concurrency"
            type="range" min="1" max="16" step="1"
            className="w-full"
            value={form.concurrency}
            onChange={set('concurrency')}
          />
        </div>

        <div className="md:col-span-2">
          <span className={LABEL}>执行方式</span>
          <div className="flex flex-wrap gap-2">
            {[
              { id: 'now', label: '立即执行' },
              { id: 'scheduled', label: '定时启动' },
            ].map((opt) => (
              <button
                key={opt.id}
                type="button"
                aria-pressed={form.mode === opt.id}
                onClick={() => setForm((prev) => ({ ...prev, mode: opt.id }))}
                className={`rounded-xl border px-4 py-2 text-sm font-semibold ${
                  form.mode === opt.id
                    ? 'border-transparent bg-primary text-white'
                    : 'border-border bg-surface text-text/70 hover:bg-surface-hover'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {form.mode === 'scheduled' && (
          <>
            <div>
              <label htmlFor="admin-start-at" className={LABEL}>启动时间 <span className="text-primary">*</span></label>
              <input
                id="admin-start-at"
                type="datetime-local"
                className={INPUT}
                value={form.start_at}
                onChange={set('start_at')}
              />
            </div>
            <div>
              <label htmlFor="admin-stop-at" className={LABEL}>停止时间（可选）</label>
              <input
                id="admin-stop-at"
                type="datetime-local"
                className={INPUT}
                value={form.stop_at}
                onChange={set('stop_at')}
              />
            </div>
            <div>
              <label htmlFor="admin-start-jitter" className={LABEL}>启动波动：±{form.start_jitter_min} 分钟</label>
              <input
                id="admin-start-jitter"
                type="range" min="0" max="120" step="1"
                className="w-full"
                value={form.start_jitter_min}
                onChange={set('start_jitter_min')}
              />
            </div>
            <div>
              <label htmlFor="admin-stop-jitter" className={LABEL}>停止波动：±{form.stop_jitter_min} 分钟</label>
              <input
                id="admin-stop-jitter"
                type="range" min="0" max="120" step="1"
                className="w-full"
                value={form.stop_jitter_min}
                onChange={set('stop_jitter_min')}
              />
            </div>
          </>
        )}
      </div>

      <div className="mt-6 flex justify-end gap-2">
        <button type="button" className={BTN_PRIMARY} onClick={() => { void submit() }} disabled={submitting}>
          <PlayCircle className="h-4 w-4" aria-hidden="true" />
          {submitting ? '提交中...' : form.mode === 'scheduled' ? '加入定时队列' : '启动任务'}
        </button>
      </div>
    </Card>
  )
}

function SystemStatusPanel({ health, onRefresh }) {
  if (!health) {
    return (
      <Card padding="compact">
        <EmptyState title="暂无系统状态数据" hint="点击刷新重新探测。" />
      </Card>
    )
  }

  const { checks = [], counters = {}, overall, version, profile, storage_backend } = health

  return (
    <div className="space-y-6">
      <Card padding="compact">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-lg font-semibold text-text">
              <Server className="h-5 w-5 text-primary" aria-hidden="true" />
              系统状态
            </h2>
            <p className="mt-1 text-xs text-text-muted">
              profile {profile} · {storage_backend} · v{version}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${
                overall === 'ok'
                  ? 'border-success/30 bg-success-surface text-success'
                  : 'border-danger/30 bg-danger-surface text-danger'
              }`}
            >
              {overall === 'ok'
                ? <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                : <XCircle className="h-3.5 w-3.5" aria-hidden="true" />}
              {overall}
            </span>
            <button type="button" className={BTN_GHOST} onClick={onRefresh}>
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              重新探测
            </button>
          </div>
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-3">
          <KPI label="活跃任务" value={counters.active_tasks} accent="text-success" sub="active tasks" />
          <KPI label="定时队列" value={counters.scheduled_tasks} accent="text-warning" sub="scheduled" />
          <KPI label="注册用户" value={counters.users} accent="text-primary" sub="users" />
        </div>
      </Card>

      <Card padding="compact">
        <h2 className="mb-4 text-lg font-semibold text-text">健康检查项</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-xs uppercase tracking-wider text-text-muted">
              <tr>
                <th className="px-3 py-3">检查项</th>
                <th className="px-3 py-3">状态</th>
                <th className="px-3 py-3">详情</th>
              </tr>
            </thead>
            <tbody>
              {checks.map((check) => (
                <tr key={check.name} className="border-b border-border/50">
                  <td className="px-3 py-3 font-mono text-xs text-text">{check.name}</td>
                  <td className="px-3 py-3">
                    <StatusBadge
                      status={check.status === 'ok' ? 'success' : check.status === 'warning' ? 'paused' : 'failed'}
                      label={check.status}
                    />
                  </td>
                  <td className="px-3 py-3 font-mono text-xs text-text/70">{check.detail || '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}

export default function Admin() {
  const toast = useToast()
  const [view, setView] = useState('overview')
  const [overview, setOverview] = useState(null)
  const [users, setUsers] = useState([])
  const [tasks, setTasks] = useState([])
  const [scheduled, setScheduled] = useState([])
  const [events, setEvents] = useState([])
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [stoppingId, setStoppingId] = useState(null)

  const handleApiError = useCallback((err, fallback) => {
    if (err?.status === 403) {
      setError('admin_forbidden')
      return
    }
    toast.error(err?.message || fallback)
  }, [toast])

  const loadOverview = useCallback(async () => {
    try {
      const resp = await api('/admin/overview')
      if (resp?.status === 'success') setOverview(resp.data)
    } catch (err) {
      if (err?.status === 403) {
        setError('admin_forbidden')
        return
      }
      setError(err?.message || '加载总览失败')
    }
  }, [])

  const loadUsers = useCallback(async () => {
    try {
      const resp = await api('/admin/users?limit=200')
      if (resp?.status === 'success') setUsers(resp.data)
    } catch (err) {
      handleApiError(err, '加载用户列表失败')
    }
  }, [handleApiError])

  const loadTasks = useCallback(async () => {
    try {
      const params = statusFilter ? `?status=${statusFilter}` : '?limit=100'
      const resp = await api(`/admin/tasks${params}`)
      if (resp?.status === 'success') setTasks(resp.data)
    } catch (err) {
      handleApiError(err, '加载任务列表失败')
    }
  }, [statusFilter, handleApiError])

  // The queue and overview panels always want scheduled rows regardless of the
  // table's status filter, so they query independently.
  const loadScheduled = useCallback(async () => {
    try {
      const resp = await api(`/admin/tasks?status=${SCHEDULED_STATUS}&limit=200`)
      if (resp?.status === 'success') setScheduled(resp.data)
    } catch (err) {
      handleApiError(err, '加载定时队列失败')
    }
  }, [handleApiError])

  const loadEvents = useCallback(async () => {
    try {
      const resp = await api('/admin/events?limit=60')
      if (resp?.status === 'success') setEvents(resp.data)
    } catch (err) {
      handleApiError(err, '加载系统事件失败')
    }
  }, [handleApiError])

  const loadHealth = useCallback(async () => {
    try {
      const resp = await api('/admin/health')
      if (resp?.status === 'success') setHealth(resp.data)
    } catch (err) {
      handleApiError(err, '加载系统状态失败')
    }
  }, [handleApiError])

  const reloadAll = useCallback(
    () => Promise.all([loadOverview(), loadUsers(), loadTasks(), loadScheduled()]),
    [loadOverview, loadUsers, loadTasks, loadScheduled],
  )

  useEffect(() => {
    setLoading(true)
    Promise.all([loadOverview(), loadUsers(), loadTasks(), loadScheduled(), loadEvents(), loadHealth()])
      .finally(() => setLoading(false))
  }, [loadOverview, loadUsers, loadTasks, loadScheduled, loadEvents, loadHealth])

  // Only refresh what the visible tab shows, so idle tabs cost nothing.
  useEffect(() => {
    const intervalFor = {
      overview: () => { void loadOverview(); void loadScheduled(); void loadEvents() },
      queue: loadScheduled,
      tasks: loadTasks,
      status: loadHealth,
    }[view]
    if (!intervalFor) return undefined
    const id = setInterval(intervalFor, 30000)
    return () => clearInterval(id)
  }, [view, loadOverview, loadScheduled, loadEvents, loadTasks, loadHealth])

  const handleStop = async (taskId) => {
    if (!window.confirm(`确定要停止任务 ${taskId}？`)) return
    setStoppingId(taskId)
    try {
      const resp = await api(`/admin/task/${taskId}/stop`, { method: 'POST' })
      if (resp?.status === 'success') {
        toast.success(`任务 ${taskId} 已停止`)
        await reloadAll()
      }
    } catch (err) {
      toast.error(err?.message || '停止任务失败')
    } finally {
      setStoppingId(null)
    }
  }

  const recentActive = useMemo(
    () => tasks.filter((t) => isLive(t.status)).slice(0, 6),
    [tasks],
  )

  if (error === 'admin_forbidden') {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <EmptyState
          title="需要管理员权限"
          hint="你当前不是管理员，无法访问此页面。如需权限，请联系系统管理员在 ADMIN_EMAILS 中添加你的邮箱。"
        />
      </div>
    )
  }

  if (loading) {
    return (
      <div className="animate-pulse space-y-4">
        <div className="h-8 w-48 rounded-xl bg-surface-hover" />
        <div className="grid grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => <div key={i} className="h-32 rounded-xl bg-surface-hover" />)}
        </div>
        <div className="h-64 rounded-xl bg-surface-hover" />
      </div>
    )
  }

  const queueCount = scheduled.length || overview?.scheduled_tasks || 0

  const TABS = [
    { id: 'overview', label: '总览' },
    { id: 'users', label: '用户管理' },
    { id: 'tasks', label: '任务总览' },
    { id: 'queue', label: '定时队列', count: queueCount },
    { id: 'start', label: '发起任务' },
    { id: 'status', label: '系统状态' },
  ]

  return (
    <div className="space-y-6">
      <section className={CARD}>
        <h1 className="text-2xl font-bold text-text">管理面板</h1>
        <p className="mt-1 text-sm text-text-muted">跨用户任务总览与系统概览</p>

        <div className="mt-5 flex flex-wrap gap-2" role="tablist" aria-label="管理面板视图">
          {TABS.map((tab) => {
            const active = view === tab.id
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setView(tab.id)}
                className={`inline-flex min-h-[38px] items-center gap-2 rounded-xl border px-4 text-sm font-semibold transition-colors ${
                  active
                    ? 'border-transparent bg-primary text-white shadow-sm'
                    : 'border-border bg-surface text-text/70 hover:bg-surface-hover'
                }`}
              >
                {tab.label}
                {tab.count > 0 && (
                  <span
                    className={`rounded-full px-2 py-0.5 font-mono text-[11px] ${
                      active ? 'bg-white/20 text-white' : 'bg-surface-hover text-text-muted'
                    }`}
                  >
                    {tab.count}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </section>

      {view === 'overview' && (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <KPI label="用户" value={overview?.users} accent="text-primary" sub="注册用户总数" />
            <KPI label="运行中" value={overview?.active_tasks} accent="text-success" sub="active tasks" />
            <KPI label="定时队列" value={overview?.scheduled_tasks} accent="text-warning" sub="scheduled" />
            <KPI label="24h 失败" value={overview?.failed_24h} accent="text-danger" sub="failed in 24h" />
          </div>

          <div className="grid gap-6 lg:grid-cols-[1.35fr_1fr]">
            <Card padding="compact">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="flex items-center gap-2 text-lg font-semibold text-text">
                  <Activity className="h-5 w-5 text-success" aria-hidden="true" />
                  实时任务流
                </h2>
                <span className="font-mono text-xs text-text-muted">30s 轮询</span>
              </div>
              {recentActive.length === 0 ? (
                <EmptyState title="当前没有进行中的任务" />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="border-b border-border text-xs uppercase tracking-wider text-text-muted">
                      <tr>
                        <th className="px-3 py-3">用户</th>
                        <th className="px-3 py-3">状态</th>
                        <th className="px-3 py-3">进度</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentActive.map((task) => (
                        <tr key={task.task_id} className="border-b border-border/50">
                          <td className="px-3 py-3 font-medium text-text">
                            {task.username || task.user_id || '--'}
                          </td>
                          <td className="px-3 py-3"><StatusBadge status={task.status} /></td>
                          <td className="px-3 py-3"><ProgressBar progress={task.progress} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card padding="compact">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-lg font-semibold text-text">系统事件</h2>
                <button
                  type="button"
                  className="font-mono text-xs text-text-muted hover:text-text"
                  onClick={() => { void loadEvents() }}
                >
                  刷新
                </button>
              </div>
              {events.length === 0 ? (
                <EmptyState title="暂无任务日志" hint="任务开始运行后，日志会汇总到这里。" />
              ) : (
                <div className="max-h-80 space-y-0 overflow-y-auto">
                  {events.slice(0, 20).map((event, index) => (
                    <div
                      key={`${event.task_id}-${event.timestamp}-${index}`}
                      className="flex gap-3 border-b border-border/40 py-2 font-mono text-xs last:border-b-0"
                    >
                      <span className="shrink-0 text-text-muted">{formatClock(event.timestamp)}</span>
                      <span className={`w-14 shrink-0 uppercase ${LEVEL_TONE[event.level] || 'text-text-muted'}`}>
                        {event.level}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-text/80" title={event.message}>
                        {event.message}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </>
      )}

      {view === 'users' && (
        <Card padding="compact">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h2 className="flex items-center gap-2 text-lg font-semibold text-text">
              <Users className="h-5 w-5 text-primary" aria-hidden="true" />
              用户管理
            </h2>
            <span className="font-mono text-xs text-text-muted">{users.length} accounts</span>
          </div>
          {users.length === 0 ? (
            <EmptyState title="暂无用户数据" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-border text-xs uppercase tracking-wider text-text-muted">
                  <tr>
                    <th className="px-3 py-3">用户名</th>
                    <th className="px-3 py-3">邮箱</th>
                    <th className="px-3 py-3">租户库</th>
                    <th className="px-3 py-3">注册时间</th>
                    <th className="px-3 py-3">活跃任务</th>
                    <th className="px-3 py-3">定时任务</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr key={user.id} className="border-b border-border/50 hover:bg-surface-hover/50">
                      <td className="px-3 py-3 font-medium text-text">{user.username || '--'}</td>
                      <td className="px-3 py-3 font-mono text-xs text-text/70">{user.email || '--'}</td>
                      <td className="px-3 py-3 font-mono text-xs text-text-muted">{user.tenant_db_name || '--'}</td>
                      <td className="px-3 py-3 text-xs text-text/70">{formatTime(user.created_at)}</td>
                      <td className="px-3 py-3 font-mono font-semibold text-text">{user.active_tasks ?? 0}</td>
                      <td className="px-3 py-3 font-mono font-semibold text-warning">{user.scheduled_tasks ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {view === 'tasks' && (
        <Card padding="compact">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold text-text">任务总览</h2>
            <div className="flex items-center gap-2">
              <select className={INPUT} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="">全部状态</option>
                <option value="running">running</option>
                <option value="pending">pending</option>
                <option value="paused">paused</option>
                <option value="cancelling">cancelling</option>
                <option value="scheduled">scheduled</option>
                <option value="completed">completed</option>
                <option value="failed">failed</option>
                <option value="cancelled">cancelled</option>
                <option value="error">error</option>
              </select>
              <button type="button" className={BTN_GHOST} onClick={() => { void loadTasks() }}>
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                刷新
              </button>
            </div>
          </div>
          <TaskTable
            tasks={tasks}
            onStop={handleStop}
            stoppingId={stoppingId}
            emptyTitle="暂无任务数据"
          />
        </Card>
      )}

      {view === 'queue' && (
        <Card padding="compact">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="flex items-center gap-2 text-lg font-semibold text-text">
                <Timer className="h-5 w-5 text-warning" aria-hidden="true" />
                定时队列
              </h2>
              <p className="mt-1 text-xs text-text-muted">
                等待调度循环触发的任务，每 30 秒自动刷新；倒计时每秒更新。
              </p>
            </div>
            <button type="button" className={BTN_GHOST} onClick={() => { void loadScheduled() }}>
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              刷新
            </button>
          </div>

          {scheduled.length === 0 ? (
            <EmptyState
              icon={CalendarClock}
              title="定时队列为空"
              hint="用户在「学习通泛雅」页选择「定时启动」，或在「发起任务」页选定时模式后，任务会出现在这里。"
            />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {scheduled.map((task) => (
                <ScheduledCard
                  key={task.task_id}
                  task={task}
                  onStop={handleStop}
                  stopping={stoppingId === task.task_id}
                />
              ))}
            </div>
          )}
        </Card>
      )}

      {view === 'start' && (
        <StartTaskPanel
          users={users}
          onStarted={() => { void reloadAll() }}
        />
      )}

      {view === 'status' && (
        <SystemStatusPanel health={health} onRefresh={() => { void loadHealth() }} />
      )}
    </div>
  )
}
