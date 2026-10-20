import { useEffect, useMemo, useRef, useState } from 'react'
import { CARD, getCourseId, getCourseName } from '../utils'


export default function CourseListSection({
  courses, selectedCourses, setSelectedCourses, chapters, expanded,
  toggleExpand, loadCourses, queuedCourses = [], onConfirmQueue,
}) {


  const [refreshing, setRefreshing] = useState(false)
  const refreshInFlightRef = useRef(false)
  const mountedRef = useRef(true)


  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])


  const handleRefresh = async () => {
    if (refreshInFlightRef.current) return

    refreshInFlightRef.current = true
    setRefreshing(true)
    try {
      await loadCourses()
    } finally {
      refreshInFlightRef.current = false
      if (mountedRef.current) setRefreshing(false)
    }
  }


  const courseIds = useMemo(() => courses.map(getCourseId).filter(Boolean), [courses])


  const allSelected = useMemo(


    () => courseIds.length > 0 && courseIds.every((courseId) => selectedCourses.includes(courseId)),


    [courseIds, selectedCourses]


  )


  return (
    <section className={CARD}>


      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">


        <h2 className="text-xl font-semibold text-text">课程列表</h2>


        <div className="flex gap-2">


          <button


            type="button"


            className="min-h-[44px] cursor-pointer rounded-lg border border-border px-3 text-sm"


            onClick={() => setSelectedCourses(allSelected ? [] : courseIds)}


          >


            {allSelected ? '取消全选' : '全选课程'}


          </button>


          <button


            type="button"


            className="min-h-[44px] cursor-pointer rounded-lg border border-border px-3 text-sm"
            disabled={refreshing}
            aria-busy={refreshing}


            onClick={() => {


              void handleRefresh()


            }}


          >


            {refreshing ? '刷新中...' : '刷新课程'}


          </button>


        </div>


      </div>


      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border/40 bg-surface-hover/50 px-3 py-2">
        <p className="text-sm text-text/80">
          已勾选 <span className="font-semibold text-primary">{selectedCourses.length}</span> 门课程
          {queuedCourses.length > 0 && (
            <span className="ml-2 text-xs text-text-muted">
              队列中 {queuedCourses.length} 门
            </span>
          )}
        </p>
        <button
          type="button"
          disabled={selectedCourses.length === 0}
          onClick={() => onConfirmQueue?.(selectedCourses)}
          className="min-h-[36px] cursor-pointer rounded-lg bg-primary px-4 text-sm font-medium text-white transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:bg-text-muted"
        >
          确定加入队列
        </button>
      </div>


      <div className="max-h-80 space-y-2 overflow-y-auto">


        {courses.map((course) => {


          const courseId = getCourseId(course)


          const isExpanded = expanded.has(courseId)
          // 1-based position in the tick order; this is the order the queue runs in.
          const queuePosition = selectedCourses.indexOf(courseId) + 1


          return (


            <div key={courseId || Math.random()} className="rounded-xl border border-border/30 bg-surface/60 p-3">


              <div className="flex items-center gap-3">


                <input


                  type="checkbox"


                  className="h-5 w-5"


                  checked={selectedCourses.includes(courseId)}


                  onChange={() => {


                    setSelectedCourses((prev) =>


                      prev.includes(courseId) ? prev.filter((id) => id !== courseId) : [...prev, courseId]


                    )


                  }}


                />


                {queuePosition > 0 && (
                  <span
                    aria-label={`队列第 ${queuePosition} 位`}
                    className="inline-grid h-6 w-6 shrink-0 place-items-center rounded-full bg-primary/15 font-mono text-xs font-semibold text-primary"
                  >
                    {queuePosition}
                  </span>
                )}


                <div className="flex-1">


                  <p className="font-semibold text-text">{getCourseName(course)}</p>


                  <p className="text-xs text-text-muted">{courseId || '--'}</p>


                </div>


                <button


                  type="button"


                  className="min-h-[44px] min-w-[44px] cursor-pointer rounded-lg border border-border px-2 text-sm"


                  onClick={() => {


                    void toggleExpand(course)


                  }}


                >


                  {isExpanded ? '收起' : '章节'}


                </button>


              </div>


              {isExpanded && (


                <div className="mt-2 space-y-1 text-sm text-text/70">


                  {(chapters[courseId] || []).map((chapter) => (


                    <div key={`${courseId}-${chapter.id || chapter.name}`} className="rounded-lg bg-surface px-3 py-2">


                      {chapter.title || chapter.name}


                    </div>


                  ))}


                  {(chapters[courseId] || []).length === 0 && (


                    <div className="text-xs text-text-muted">暂无章节数据</div>


                  )}


                </div>


              )}


            </div>


          )


        })}


      </div>


    </section>
  )


}
