import { useCallback, useEffect, useState } from 'react'
import { api } from '../utils/api'
import {
  Card,
  EmptyState,
  StatusBadge,
  useToast,
} from '../components'

const CARD = 'rounded-2xl border border-border/20 bg-surface/80 p-6 shadow-lg backdrop-blur-lg'

function KPI({ label, value, accent, sub }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-5 space-y-1">
      <p className="text-xs font-semibold uppercase tracking-widest text-text-muted">{label}</p>
      <p className={`text-3xl font-bold ${accent || 'text-text'}`}>{value ?? '--'}</p>
      {sub && <p className="text-xs text-text-muted">{sub}</p>}
    </div>
  )
}

export default function Admin() {
  const toast = useToast()
  const [overview, setOverview] = useState(null)
  const [users, setUsers] = useState([])
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [stoppingId, setStoppingId] = useState(null)

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
      if (err?.status === 403) {
        setError('admin_forbidden')
        return
      }
      toast.error(err?.message || '加载用户列表失败')
    }
  }, [toast])

  const loadTasks = useCallback(async () => {
    try {
      const params = statusFilter ? `?status=${statusFilter}` : '?limit=100'
      const resp = await api(`/admin/tasks${params}`)
      if (resp?.status === 'success') setTasks(resp.data)
    } catch (err) {
      if (err?.status === 403) {
        setError('admin_forbidden')
        return
      }
      toast.error(err?.message || '加载任务列表失败')
    }
  }, [statusFilter, toast])

  useEffect(() => {
    setLoading(true)
    Promise.all([loadOverview(), loadUsers(), loadTasks()]).finally(() => setLoading(false))
  }, [loadOverview, loadUsers, loadTasks])

  const handleStop = async (taskId) => {
    if (!window.confirm(`确定要停止任务 ${taskId}？`)) return
    setStoppingId(taskId)
    try {
      const resp = await api(`/admin/task/${taskId}/stop`, { method: 'POST' })
      if (resp?.status === 'success') {
        toast.success(`任务 ${taskId} 已停止`)
        await loadTasks()
      }
    } catch (err) {
      toast.error(err?.message || '停止任务失败')
    } finally {
      setStoppingId(null)
    }
  }

  const formatTime = (v) => {
    if (!v) return '--'
    try {
      return new Date(v).toLocaleString('zh-CN', { hour12: false })
    } catch {
      return String(v)
    }
  }

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
      <div className="space-y-4 animate-pulse">
        <div className="h-8 w-48 rounded-xl bg-surface-hover" />
        <div className="grid grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-32 rounded-xl bg-surface-hover" />
          ))}
        </div>
        <div className="h-64 rounded-xl bg-surface-hover" />
      </div>
    )
  }

  const ACTIVE_STATUSES = new Set(['running', 'pending', 'paused', 'cancelling', 'scheduled'])
  const isLive = (status) => ACTIVE_STATUSES.has(String(status || '').toLowerCase())

  return (
    <div className="space-y-6">
      <section className={CARD}>
        <h1 className="text-2xl font-bold text-text">管理面板</h1>
        <p className="mt-1 text-sm text-text-muted">跨用户任务总览与系统概览</p>
      </section>

      {/* KPI Row */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KPI label="用户" value={overview?.users} accent="text-primary" sub="注册用户总数" />
        <KPI label="运行中" value={overview?.active_tasks} accent="text-success" sub="active tasks" />
        <KPI label="定时队列" value={overview?.scheduled_tasks} accent="text-warning" sub="scheduled" />
        <KPI label="24h 失败" value={overview?.failed_24h} accent="text-danger" sub="failed in 24h" />
      </div>

      {/* Users Table */}
      <Card padding="compact">
        <h2 className="mb-4 text-lg font-semibold text-text">用户管理</h2>
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
                    <td className="px-3 py-3"><span className="font-mono font-semibold text-text">{user.active_tasks ?? 0}</span></td>
                    <td className="px-3 py-3"><span className="font-mono font-semibold text-warning">{user.scheduled_tasks ?? 0}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Tasks Table */}
      <Card padding="compact">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-text">任务总览</h2>
          <div className="flex items-center gap-2">
            <select
              className="rounded-xl border border-border bg-surface px-3 py-2 text-sm text-text"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
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
            <button
              type="button"
              onClick={() => { loadTasks() }}
              className="rounded-xl border border-border bg-surface px-4 py-2 text-sm font-medium text-text/70 hover:bg-surface-hover"
            >
              刷新
            </button>
          </div>
        </div>

        {tasks.length === 0 ? (
          <EmptyState title="暂无任务数据" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border text-xs uppercase tracking-wider text-text-muted">
                <tr>
                  <th className="px-3 py-3">任务 ID</th>
                  <th className="px-3 py-3">用户</th>
                  <th className="px-3 py-3">状态</th>
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
                    <td className="px-3 py-3 text-xs text-text/70 max-w-xs truncate">{task.message || '--'}</td>
                    <td className="px-3 py-3 text-xs text-text/70">{formatTime(task.updated_at)}</td>
                    <td className="px-3 py-3">
                      {isLive(task.status) && (
                        <button
                          type="button"
                          onClick={() => { void handleStop(task.task_id) }}
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
        )}
      </Card>
    </div>
  )
}
