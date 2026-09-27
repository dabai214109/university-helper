import { useState } from 'react'
import { CARD, DONE_STATUSES, toNum, normalizeCourseText } from '../utils'
import CourseQueue from './CourseQueue'


export default function TaskControlSection({
  taskId, taskStatus, loading, isRunning, statusText,
  startTask, controlTask, controlLoading = {},
}) {


  const progress = taskStatus?.progress || {}
  const schedule = taskStatus?.schedule || null
  const currentCourseName = normalizeCourseText(progress.current_course)
  const videoProgress = progress.video_progress && typeof progress.video_progress === 'object' ? progress.video_progress : null
  const videoCurrent = Math.max(0, toNum(videoProgress?.current))
  const videoDuration = Math.max(0, toNum(videoProgress?.duration))
  const videoPercent = videoDuration > 0 ? Math.min(100, Math.max(0, (videoCurrent / videoDuration) * 100)) : 0
  const coursePercent = toNum(progress.total) > 0 ? Math.min(100, Math.max(0, (toNum(progress.completed) / Math.max(toNum(progress.total), 1)) * 100)) : 0
  const chapterPercent = toNum(progress.total_chapters) > 0 ? Math.min(100, Math.max(0, (toNum(progress.completed_chapters) / Math.max(toNum(progress.total_chapters), 1)) * 100)) : 0

  const isScheduled = statusText === 'scheduled'
  const isRecurring = statusText === 'recurring'
  // A recurring template has no single run in flight, so its card shows the
  // rule and the next firing instead of the per-run progress panels.
  const isPending = isScheduled || isRecurring
  // Only a *pending run* blocks starting another task. A recurring template is
  // long-lived and explicitly does not occupy the user's one-active-task slot
  // (see task_admission.RECURRING_TASK_STATUSES), so the start button must stay
  // available — otherwise the template would lock the page forever.
  const blocksNewTask = isScheduled
  const repeatLabel = schedule?.repeat === 'daily'
    ? '每天'
    : schedule?.repeat === 'every_other_day'
      ? '隔天'
      : ''

  // Reschedule modal state
  const [showReschedule, setShowReschedule] = useState(false)
  const [rescheduleAt, setRescheduleAt] = useState('')

  const openReschedule = () => {
    const start = schedule?.start_at || ''
    try {
      setRescheduleAt(new Date(start).toISOString().slice(0, 16))
    } catch {
      setRescheduleAt('')
    }
    setShowReschedule(true)
  }

  const doReschedule = () => {
    if (!rescheduleAt) return
    const body = {
      start_at: new Date(rescheduleAt).toISOString(),
    }
    void controlTask('reschedule', body)
    setShowReschedule(false)
  }


  return (
    <section className={CARD}>

      {showReschedule && (
        <div className="mb-4 rounded-xl border border-warning/30 bg-warning-surface p-4">
          <label htmlFor="rs-input" className="mb-2 block text-sm font-medium text-text/80">
            新的启动时间
          </label>
          <input
            id="rs-input"
            type="datetime-local"
            className="w-full rounded-xl border border-border bg-surface px-4 py-3 mb-3"
            value={rescheduleAt}
            onChange={(e) => setRescheduleAt(e.target.value)}
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={doReschedule}
              className="min-h-[36px] cursor-pointer rounded-xl bg-primary px-4 py-2 text-sm text-white"
            >
              保存改期
            </button>
            <button
              type="button"
              onClick={() => setShowReschedule(false)}
              className="min-h-[36px] cursor-pointer rounded-xl border border-border bg-surface px-4 py-2 text-sm text-text/70"
            >
              取消
            </button>
          </div>
        </div>
      )}


      <div className="mb-4 flex flex-wrap gap-2">


        {!blocksNewTask && (
          <button


            type="button"


            onClick={() => {


              void startTask()


            }}


            disabled={loading || isRunning}


            className="min-h-[44px] cursor-pointer rounded-xl bg-primary px-6 py-3 text-white disabled:cursor-not-allowed disabled:bg-text-muted"


          >


            {loading ? '启动中...' : '开始刷课'}


          </button>
        )}


        {isPending && (
          <button
            type="button"
            onClick={() => {
              void controlTask('start-now')
            }}
            disabled={Boolean(controlLoading?.['start-now'])}
            className="min-h-[44px] cursor-pointer rounded-xl bg-primary px-6 py-3 text-white disabled:cursor-not-allowed disabled:bg-text-muted"
          >
            {isRecurring ? '立即执行一次' : '立即开始'}
          </button>
        )}


        {!isPending && (
          <button


            type="button"


            onClick={() => {


              void controlTask('pause')


            }}


            disabled={statusText !== 'running' || Boolean(controlLoading?.pause)}


            className="min-h-[44px] cursor-pointer rounded-xl border border-amber-300 px-4 py-2 disabled:cursor-not-allowed disabled:border-border"


          >


            暂停


          </button>
        )}


        <button


          type="button"


          onClick={() => {


            void controlTask('resume')


          }}


          disabled={statusText !== 'paused' || Boolean(controlLoading?.resume)}


          className="min-h-[44px] cursor-pointer rounded-xl border border-success px-4 py-2 disabled:cursor-not-allowed disabled:border-border"


        >


          继续


        </button>


        {isScheduled && (
          <button
            type="button"
            onClick={openReschedule}
            disabled={Boolean(controlLoading?.reschedule)}
            className="min-h-[44px] cursor-pointer rounded-xl border border-warning/30 px-4 py-2 disabled:cursor-not-allowed disabled:border-border"
          >
            改期
          </button>
        )}


        <button


          type="button"


          onClick={() => {
            if (window.confirm('确定要停止当前刷课任务吗？')) {
              void controlTask('stop')
            }
          }}


          disabled={!taskId || statusText === 'cancelling' || DONE_STATUSES.has(statusText) || Boolean(controlLoading?.stop)}


          className="min-h-[44px] cursor-pointer rounded-xl border border-danger/30 px-4 py-2 disabled:cursor-not-allowed disabled:border-border"


        >


          停止


        </button>


      </div>

      {isRecurring && schedule && (
        <div className="mb-4 rounded-xl border border-primary/20 bg-primary/5 p-4 space-y-1">
          <p className="text-sm font-semibold text-primary">🔁 周期任务</p>
          <p className="text-xs text-text/70">
            执行周期：{repeatLabel || '--'} {schedule.time_of_day || '--'}
            {schedule.start_jitter_min > 0 && <span className="text-primary/70"> ±{schedule.start_jitter_min}min</span>}
          </p>
          {schedule.next_fire_at && (
            <p className="text-xs text-text/70">
              下次执行：{new Date(schedule.next_fire_at).toLocaleString('zh-CN', { hour12: false })}
            </p>
          )}
          {schedule.max_duration_min > 0 && (
            <p className="text-xs text-text/70">单次最长运行：{schedule.max_duration_min} 分钟</p>
          )}
          <p className="text-xs text-text/70">
            已执行 {toNum(schedule.fired_count)} 次
            {schedule.last_fired_at && `，上次 ${new Date(schedule.last_fired_at).toLocaleString('zh-CN', { hour12: false })}`}
          </p>
          <p className="pt-1 text-xs text-text-muted">
            每次到点会生成一条独立的刷课记录；此卡片是周期规则本身。
          </p>
        </div>
      )}

      {isScheduled && schedule && (
        <div className="mb-4 rounded-xl border border-warning/20 bg-warning-surface/40 p-4 space-y-1">
          <p className="text-sm font-semibold text-warning/80">⏳ 定时任务</p>
          <p className="text-xs text-text/70">
            计划启动：{schedule.start_at ? new Date(schedule.start_at).toLocaleString('zh-CN', { hour12: false }) : '--'}
            {schedule.start_jitter_min > 0 && <span className="text-warning/60"> ±{schedule.start_jitter_min}min</span>}
          </p>
          {schedule.stop_at && (
            <p className="text-xs text-text/70">
              计划停止：{new Date(schedule.stop_at).toLocaleString('zh-CN', { hour12: false })}
              {schedule.stop_jitter_min > 0 && <span className="text-warning/60"> ±{schedule.stop_jitter_min}min</span>}
            </p>
          )}
          {schedule.fire_at && (
            <p className="text-xs text-text/70">
              实际启动：{new Date(schedule.fire_at).toLocaleString('zh-CN', { hour12: false })}
            </p>
          )}
        </div>
      )}


      <div className="grid gap-3 text-sm md:grid-cols-4">


        <div className="rounded-xl border border-border bg-surface p-3">


          <p className="text-text-muted">任务状态</p>


          <p className="font-semibold">{taskStatus?.status || 'idle'}</p>


        </div>


        <div className="rounded-xl border border-border bg-surface p-3">


          <p className="text-text-muted">当前任务</p>


          <p className="font-semibold">{taskStatus?.current_task || '--'}</p>


        </div>


        <div className="rounded-xl border border-border bg-surface p-3">


          <p className="text-text-muted">课程进度</p>


          <p className="font-semibold">


            {toNum(progress.completed)}/{toNum(progress.total)}（失败 {toNum(progress.failed)}）


          </p>


        </div>


        <div className="rounded-xl border border-border bg-surface p-3">


          <p className="text-text-muted">章节进度</p>


          <p className="font-semibold">


            {toNum(progress.completed_chapters)}/{toNum(progress.total_chapters)}


          </p>


        </div>


      </div>


      {!isPending && (
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <div className="rounded-xl border border-border bg-surface p-4">
            <p className="text-sm text-text-muted">当前课程</p>
            <p className="mt-1 font-semibold text-text">{currentCourseName || '--'}</p>
            <p className="mt-3 text-sm text-text-muted">当前视频</p>
            <p className="mt-1 font-semibold text-text">{videoProgress?.name || '--'}</p>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface-hover">
              <div className="h-full rounded-full bg-primary transition-all duration-200" style={{ width: `${videoPercent}%` }} />
            </div>
            <p className="mt-2 text-xs text-text/70">播放进度：{videoCurrent.toFixed(1)} / {videoDuration.toFixed(1)} 秒（{Math.round(videoPercent)}%）</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4">
            <p className="text-sm text-text-muted">任务推进</p>
            <p className="mt-1 text-sm text-text/80">课程：{toNum(progress.completed)}/{toNum(progress.total)}（{Math.round(coursePercent)}%）</p>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-hover">
              <div className="h-full rounded-full bg-success transition-all duration-200" style={{ width: `${coursePercent}%` }} />
            </div>
            <p className="mt-3 text-sm text-text/80">章节：{toNum(progress.completed_chapters)}/{toNum(progress.total_chapters)}（{Math.round(chapterPercent)}%）</p>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-hover">
              <div className="h-full rounded-full bg-violet-500 transition-all duration-200" style={{ width: `${chapterPercent}%` }} />
            </div>
          </div>
        </div>
      )}

      <CourseQueue courses={progress.courses} />
    </section>
  )


}
