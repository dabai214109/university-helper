import { useCallback, useEffect, useRef, useState } from 'react'

import { useToast } from '../components'

import { CARD, getCourseId, mergeTaskHistory, resolveTikuToken } from './chaoxing-fanya/utils'


import useTaskConfig from './chaoxing-fanya/hooks/useTaskConfig'
import useTaskExecution from './chaoxing-fanya/hooks/useTaskExecution'
import useAuthentication from './chaoxing-fanya/hooks/useAuthentication'


import LoginSection from './chaoxing-fanya/components/LoginSection'
import CourseListSection from './chaoxing-fanya/components/CourseListSection'
import CoursePortalSection from './chaoxing-fanya/components/CoursePortalSection'
import CourseQueueSection from './chaoxing-fanya/components/CourseQueueSection'
import ConfigSection from './chaoxing-fanya/components/ConfigSection'
import TaskControlSection from './chaoxing-fanya/components/TaskControlSection'
import TaskHistorySection from './chaoxing-fanya/components/TaskHistorySection'
import LogSection from './chaoxing-fanya/components/LogSection'


export default function ChaoxingFanya() {


  const [selectedCourses, setSelectedCourses] = useState([])
  // Confirmed queue: the FIFO order the backend will run. Kept separate from
  // `selectedCourses` so ticking a box afterwards cannot silently reorder a run
  // the user already committed to.
  const [queuedCourses, setQueuedCourses] = useState([])
  const [chapters, setChapters] = useState({})
  const [expanded, setExpanded] = useState(new Set())

  // QR is the default: it never asks the user to hand over a password.
  const [loginMethod, setLoginMethod] = useState('qr')


  // Shared pollRef lives in parent to break circular dependency between hooks.
  // stopPolling is needed by useAuthentication (for auth error handling)
  // and by useTaskExecution (for polling lifecycle).
  const pollRef = useRef(null)
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])


  const toast = useToast()

  const taskConfig = useTaskConfig()


  const auth = useAuthentication({ stopPolling })

  // A returning user may still hold a live Chaoxing session (from a QR scan or
  // an earlier password login). Recover it so they are not asked to log in
  // again — the courses then decide whether the login card is shown at all.
  useEffect(() => {
    let active = true
    const restoreSession = async () => {
      const loggedIn = await auth.refreshLoginStatus()
      if (!active || !loggedIn) return
      await auth.loadCourses()
    }
    void restoreSession()
    return () => {
      active = false
    }
    // auth.refreshLoginStatus / auth.loadCourses are stable useCallbacks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Keep selections valid when a successful login/refresh replaces the course list.
  // A failed refresh leaves auth.courses untouched, so it does not discard choices.
  // The confirmed queue is filtered the same way: a course that disappeared must
  // not be submitted as a stale ID.
  useEffect(() => {
    const availableCourseIds = new Set(auth.courses.map(getCourseId).filter(Boolean))
    const prune = (prev) => {
      const next = prev.filter((courseId) => availableCourseIds.has(courseId))
      return next.length === prev.length ? prev : next
    }
    setSelectedCourses(prune)
    setQueuedCourses(prune)
  }, [auth.courses])

  // Surface auth errors / notices through the shared toast and immediately
  // clear the source so the same message can be re-announced if it recurs.
  const { error: authError, notice: authNotice, setError: authSetError, setNotice: authSetNotice } =
    auth
  useEffect(() => {
    if (authError) {
      toast.error(authError)
      authSetError('')
    }
  }, [authError, authSetError, toast])

  useEffect(() => {
    if (authNotice) {
      toast.success(authNotice)
      authSetNotice('')
    }
  }, [authNotice, authSetNotice, toast])


  const taskExec = useTaskExecution({
    callApi: auth.callApi,
    setError: auth.setError,
    setNotice: auth.setNotice,
    pollRef,
    stopPolling,
  })


  const toggleExpand = useCallback(
    async (course) => {
      const courseId = getCourseId(course)
      if (!courseId) return

      const opening = !expanded.has(courseId)
      setExpanded((prev) => {
        const next = new Set(prev)
        if (next.has(courseId)) next.delete(courseId)
        else next.add(courseId)
        return next
      })

      // Refetch on every open: 已完成 / 待解锁 change as tasks run, so a list
      // cached from the first expand would keep showing stale badges.
      if (!opening) return

      try {
        const resp = await auth.callApi(`/course/chapters/${courseId}`)
        if (!resp) return
        setChapters((prev) => ({ ...prev, [courseId]: resp.chapters || [] }))
      } catch (err) {
        auth.setError(err?.message || '获取章节失败。')
      }
    },
    [auth, expanded]
  )


  const startTask = useCallback(async () => {
    auth.setError('')
    auth.setNotice('')

    const availableCourseIds = new Set(auth.courses.map(getCourseId).filter(Boolean))
    // Run the confirmed queue, in the order it was confirmed (FIFO).
    const validSelectedCourses = queuedCourses.filter((courseId) => availableCourseIds.has(courseId))


    if (!auth.loggedIn && (!auth.username.trim() || !auth.password.trim())) {
      auth.setError('请先扫码登录，或填写账号和密码。')
      return
    }
    if (validSelectedCourses.length === 0) {
      auth.setError(
        selectedCourses.length > 0
          ? '请先点击「确定加入队列」再开始刷课。'
          : '请至少选择一门课程。'
      )
      return
    }

    // 题库 / AI 答题的必要参数。后端对 AI 和题库适配器做严格 URL 校验，
    // 缺字段会返回 422；在这里先拦下，用户才知道要填什么。
    const tikuProviders = Array.isArray(taskConfig.tikuProvider)
      ? taskConfig.tikuProvider
      : [taskConfig.tikuProvider]
    if (tikuProviders.includes('AI')) {
      if (!taskConfig.aiEndpoint.trim()) {
        auth.setError('已选择「AI 智能答题」，请填写 AI 接口地址（必须公网 http/https）。')
        return
      }
      if (!taskConfig.aiModel.trim()) {
        auth.setError('已选择「AI 智能答题」，请填写 AI 模型名（例如 gpt-4o-mini）。')
        return
      }
      if (!taskConfig.aiApiKey.trim()) {
        auth.setError('已选择「AI 智能答题」，请填写 AI API Key（在 AI 区域）。')
        return
      }
    }
    if (tikuProviders.includes('TikuAdapter') && !taskConfig.adapterUrl.trim()) {
      auth.setError('已选择「题库适配器」，请填写适配器地址（必须公网 http/https）。')
      return
    }
    // 硅基流动的地址/模型有默认值，但 Key 没有 —— 缺了会被静默禁用。
    if (tikuProviders.includes('SiliconFlow') && !taskConfig.siliconFlowKey.trim()) {
      auth.setError('已选择「硅基流动」，请填写硅基流动 API Key。')
      return
    }

    // Validate schedule inputs
    const isSchedule = taskConfig.scheduleMode === 'scheduled'
    const isRecurring = isSchedule && taskConfig.repeat !== 'once'
    if (isSchedule) {
      if (isRecurring) {
        if (!taskConfig.timeOfDay) {
          auth.setError('请选择每天的执行时间。')
          return
        }
      } else if (!taskConfig.scheduleStartAt) {
        auth.setError('请选择定时启动时间。')
        return
      }
      if (!isRecurring && taskConfig.scheduleStopAt) {
        const start = new Date(taskConfig.scheduleStartAt).getTime()
        const stop = new Date(taskConfig.scheduleStopAt).getTime()
        if (stop <= start) {
          auth.setError('停止时间必须晚于启动时间。')
          return
        }
      }
    }


    taskExec.setLoading(true)
    taskExec.setLogs([])
    taskExec.setLogCursor(0)
    taskExec.logCursorRef.current = 0


    try {
      const body = {
        platform: 'chaoxing',
        username: auth.username.trim(),
        password: auth.password,
        course_ids: validSelectedCourses,
        speed: taskConfig.speed,
        concurrency: taskConfig.concurrency,
        unopened_strategy: taskConfig.unopenedStrategy,
        tiku_config: {
          provider: (Array.isArray(taskConfig.tikuProvider)
            ? taskConfig.tikuProvider
            : [taskConfig.tikuProvider]
          ).join(','),
          // 空字符串 = 用内置默认 Token，不是"没有 token"。
          // 默认值在这里兜底，因此它不进入组件 state，也就不会渲染到界面上。
          token: resolveTikuToken(
            taskConfig.tikuToken,
            Array.isArray(taskConfig.tikuProvider) ? taskConfig.tikuProvider : [taskConfig.tikuProvider]
          ),
          coverage_threshold: taskConfig.coverageThreshold,
          judge_mapping: {
            correct: taskConfig.correctOptions
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean),
            wrong: taskConfig.wrongOptions
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean)
          },
          submit_mode: taskConfig.submitMode,
          // 只在用户真的填了才发，避免空串触发后端的 URL 校验
          ...(taskConfig.aiEndpoint.trim() ? { endpoint: taskConfig.aiEndpoint.trim() } : {}),
          ...(taskConfig.aiModel.trim() ? { model: taskConfig.aiModel.trim() } : {}),
          // AI 的 Key 单独发，避免和言溪 Token 抢同一个字段
          ...(taskConfig.aiApiKey.trim() ? { key: taskConfig.aiApiKey.trim() } : {}),
          // 硅基流动的 Key 也是独立字段（引擎读 siliconflow_key）
          ...(taskConfig.siliconFlowKey.trim()
            ? { siliconflow_key: taskConfig.siliconFlowKey.trim() }
            : {}),
          ...(taskConfig.adapterUrl.trim() ? { url: taskConfig.adapterUrl.trim() } : {})
        },
        notify_config:
          taskConfig.notifyService.trim() && taskConfig.notifyUrl.trim()
            ? {
                service: taskConfig.notifyService.trim(),
                url: taskConfig.notifyUrl.trim()
              }
            : {}
      }

      if (isSchedule) {
        body.repeat = taskConfig.repeat || 'once'
        body.start_jitter_min = taskConfig.startJitterMin
        body.stop_jitter_min = taskConfig.stopJitterMin
        if (isRecurring) {
          // A recurring run is driven by the wall-clock time; an absolute
          // start_at is neither sent nor required.
          body.time_of_day = taskConfig.timeOfDay
          if (taskConfig.repeat === 'every_other_day' && taskConfig.anchorDate) {
            body.anchor_date = taskConfig.anchorDate
          }
          if (taskConfig.maxDurationMin) {
            body.max_duration_min = taskConfig.maxDurationMin
          }
        } else {
          body.start_at = new Date(taskConfig.scheduleStartAt).toISOString()
          if (taskConfig.scheduleStopAt) {
            body.stop_at = new Date(taskConfig.scheduleStopAt).toISOString()
          }
        }
      }

      const resp = await auth.callApi('/course/start', {
        method: 'POST',
        body: JSON.stringify(body),
      })


      if (!resp) return
      if (!resp.task_id) throw new Error('未返回任务 ID，任务未启动。')


      taskExec.setTaskId(resp.task_id)
      taskExec.setTaskStatus(resp)
      taskExec.setTaskHistory((prev) =>
        mergeTaskHistory(
          prev,
          {
            ...resp,
            task_id: resp.task_id,
            status: String(resp.status || 'pending').toLowerCase(),
            updated_at: resp.updated_at || resp.updatedAt || new Date().toISOString()
          },
          true
        )
      )
      const resultStatus = String(resp.status || '').toLowerCase()
      const isRecurringResult = String(resp.schedule?.repeat || 'once') !== 'once'
      let startMsg
      if (resultStatus === 'queued') {
        startMsg = `已加入队列：${resp.task_id}（当前任务结束后自动开始）`
      } else if (resultStatus === 'recurring') {
        startMsg = `周期任务已创建：${resp.task_id}（${taskConfig.repeat === 'daily' ? '每天' : '隔天'} ${taskConfig.timeOfDay}）`
      } else if (resultStatus === 'scheduled') {
        startMsg = isRecurringResult
          ? `周期任务已创建：${resp.task_id}`
          : `定时任务已创建：${resp.task_id}（计划 ${new Date(taskConfig.scheduleStartAt).toLocaleString('zh-CN', { hour12: false })} 启动）`
      } else {
        startMsg = `任务已创建：${resp.task_id}`
      }
      taskExec.appendLogs([{ timestamp: new Date().toISOString(), level: 'success', message: startMsg }])
      if (resultStatus === 'queued') {
        toast.success('已加入队列，当前任务结束后会自动开始')
        // 队列已提交给后端，本地这份可以清掉，避免误导「还没开始」
        setQueuedCourses([])
      } else if (resultStatus === 'recurring') {
        toast.success('周期任务已创建，将按设定时间反复执行')
      } else if (resultStatus === 'scheduled') {
        toast.success('已加入定时队列')
      }
    } catch (err) {
      auth.setError(err?.message || '启动任务失败。')
    } finally {
      taskExec.setLoading(false)
    }
  }, [
    auth,
    taskExec,
    taskConfig,
    queuedCourses,
    selectedCourses,
    toast,
  ])

  const confirmQueue = useCallback(
    (courseIds) => {
      const ordered = Array.isArray(courseIds) ? courseIds.filter(Boolean) : []
      setQueuedCourses(ordered)
      if (ordered.length === 0) {
        toast.error('请至少选择一门课程。')
        return
      }
      toast.success(`已加入队列：共 ${ordered.length} 门课程，将按勾选顺序执行`)
    },
    [toast]
  )

  const clearQueue = useCallback(() => {
    setQueuedCourses([])
    toast.success('已清空队列')
  }, [toast])

  // Logging out cancels running tasks server-side, so ask first.
  const handleLogoutChaoxing = useCallback(async () => {
    const confirmed = window.confirm(
      '确定退出学习通账号？\n\n' +
        '· 正在运行 / 排队 / 定时中的刷课任务会被取消\n' +
        '· 下次使用需重新扫码或输入密码\n' +
        '· 已保存的任务记录与配置不受影响'
    )
    if (!confirmed) return

    taskExec.stopPolling?.()
    taskExec.setTaskId('')
    taskExec.setTaskStatus(null)
    taskExec.setLogs([])
    taskExec.setTaskHistory([])
    setSelectedCourses([])
    setQueuedCourses([])
    setChapters({})
    setExpanded(new Set())

    await auth.logoutChaoxing()
  }, [auth, taskExec])


  const statusText = String(taskExec.taskStatus?.status || '').toLowerCase()
  const isRunning = ['started', 'running', 'pending', 'paused', 'cancelling'].includes(statusText)

  // 标题栏上显示"当前是哪个学习通账号"。
  // 是否真的持有一个学习通会话。auth.username 只是登录表单的输入（而且会从
  // localStorage 里恢复上次用过的账号），未登录时它也可能有值 —— 不能拿它
  // 当作"已登录"的证据，否则页面会在没登录的情况下显示一个用户 ID。
  const hasSession = Boolean(auth.loggedIn || auth.courses.length > 0)
  // 昵称只有扫码登录后后端才有；密码登录不写 vault，拿不到昵称，
  // 这时退回显示账号本身。但两者都必须建立在 hasSession 之上。
  const accountLabel = hasSession ? auth.nickname || auth.username || '' : ''
  const showLogout = hasSession


  return (
    <div>
      <div className="space-y-6">
        <section className={CARD}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-baseline gap-3">
              <h1 className="text-3xl font-bold text-text">超星学习通自动刷课</h1>
              {/* 昵称只有扫码登录后后端才有；密码登录时退回显示账号本身，
                  否则这一行会空着，用户看不出当前是哪个号。 */}
              {accountLabel && (
                <span className="text-sm text-text/70">
                  用户：<span className="font-medium text-text">{accountLabel}</span>
                </span>
              )}
            </div>
            {showLogout && (
              <button
                type="button"
                onClick={() => {
                  void handleLogoutChaoxing()
                }}
                className="min-h-[40px] shrink-0 cursor-pointer rounded-xl border border-border bg-surface px-4 text-sm font-medium text-text/80 transition-colors hover:bg-surface-hover"
              >
                退出学习通账号
              </button>
            )}
          </div>
        </section>


        {auth.courses.length === 0 ? (
          <LoginSection
            username={auth.username}
            setUsername={auth.setUsername}
            password={auth.password}
            setPassword={auth.setPassword}
            loginLoading={auth.loginLoading}
            handleLogin={auth.handleLogin}
            loginMethod={loginMethod}
            setLoginMethod={setLoginMethod}
            qrLogin={auth.qrLogin}
          />
        ) : (
          <>
            <CourseListSection
              courses={auth.courses}
              selectedCourses={selectedCourses}
              setSelectedCourses={setSelectedCourses}
              queuedCourses={queuedCourses}
              onConfirmQueue={confirmQueue}
              chapters={chapters}
              expanded={expanded}
              toggleExpand={toggleExpand}
              loadCourses={auth.loadCourses}
            />

            <CoursePortalSection
              courses={auth.courses}
              selectedCourses={selectedCourses}
              setError={auth.setError}
              setNotice={auth.setNotice}
            />

            {/* 队列清单夹在「学习通课程」和参数配置之间：勾完课、确认加入后，
                视线往下就是"我排了什么"，再往下才是速度和题库。 */}
            <CourseQueueSection
              courses={auth.courses}
              queuedCourses={queuedCourses}
              onClearQueue={clearQueue}
            />


            <ConfigSection {...taskConfig} />


            <TaskControlSection
              taskId={taskExec.taskId}
              taskStatus={taskExec.taskStatus}
              loading={taskExec.loading}
              controlLoading={taskExec.controlLoading}
              isRunning={isRunning}
              queuedCount={queuedCourses.length}
              statusText={statusText}
              startTask={startTask}
              controlTask={taskExec.controlTask}
            />


            <TaskHistorySection
              taskHistory={taskExec.taskHistory}
              taskId={taskExec.taskId}
              selectTaskFromHistory={taskExec.selectTaskFromHistory}
              removeTaskFromHistory={taskExec.removeTaskFromHistory}
              removingTaskId={taskExec.removingTaskId}
            />
          </>
        )}


        <LogSection logs={taskExec.logs} />
      </div>
    </div>
  )
}
