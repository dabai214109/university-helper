import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { afterEach, describe, expect, test, vi } from 'vitest'

import CourseListSection from './CourseListSection'

const renderCourseList = (selectedCourses, loadCourses = () => Promise.resolve()) => render(
  <StrictMode>
    <CourseListSection
      courses={[{ courseId: 'course-a' }, { courseId: 'course-b' }]}
      selectedCourses={selectedCourses}
      setSelectedCourses={() => {}}
      chapters={{}}
      expanded={new Set()}
      toggleExpand={() => {}}
      loadCourses={loadCourses}
    />
  </StrictMode>,
)

const deferred = () => {
  let resolve
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

describe('CourseListSection selection state', () => {
  afterEach(() => {
    cleanup()
  })

  test('does not treat a same-length different ID set as all selected', () => {
    renderCourseList(['course-a', 'stale-course'])

    expect(screen.getByRole('button', { name: '全选课程' })).toBeInTheDocument()
  })

  test('treats every available course as selected regardless of selection order', () => {
    renderCourseList(['course-b', 'course-a'])

    expect(screen.getByRole('button', { name: '取消全选' })).toBeInTheDocument()
  })

  test('disables refresh while its request is pending', async () => {
    const pending = deferred()
    const loadCourses = vi.fn(() => pending.promise)
    renderCourseList([], loadCourses)
    const refreshButton = screen.getByRole('button', { name: '刷新课程' })
    const user = userEvent.setup()

    await user.click(refreshButton)

    expect(loadCourses).toHaveBeenCalledTimes(1)
    expect(refreshButton).toBeDisabled()
    expect(refreshButton).toHaveTextContent('刷新中...')

    await act(async () => {
      pending.resolve()
      await pending.promise
    })

    expect(refreshButton).not.toBeDisabled()
    expect(refreshButton).toHaveTextContent('刷新课程')
  })
})

describe('CourseListSection chapter badges', () => {
  afterEach(() => {
    cleanup()
  })

  const renderExpanded = (chapters) =>
    render(
      <CourseListSection
        courses={[{ courseId: 'course-a' }]}
        selectedCourses={[]}
        setSelectedCourses={() => {}}
        chapters={{ 'course-a': chapters }}
        expanded={new Set(['course-a'])}
        toggleExpand={() => {}}
        loadCourses={() => Promise.resolve()}
      />,
    )

  test('shows 已完成 and 待解锁 from the backend flags', () => {
    renderExpanded([
      { id: 'p1', title: '1.1 函数', has_finished: true, need_unlock: false },
      { id: 'p2', title: '1.2 极限', has_finished: false, need_unlock: true },
      { id: 'p3', title: '1.3 连续', has_finished: false, need_unlock: false },
    ])

    expect(screen.getByText('1.1 函数')).toBeInTheDocument()
    expect(screen.getAllByText('已完成')).toHaveLength(1)
    expect(screen.getAllByText('待解锁')).toHaveLength(1)
    // The untouched chapter must not borrow a badge from its neighbours.
    expect(screen.getAllByText('已完成')).toHaveLength(1)
  })

  test('numbers chapters and falls back to the name field', () => {
    renderExpanded([{ id: 'p1', name: '命名回退' }])

    expect(screen.getByText('1.')).toBeInTheDocument()
    expect(screen.getByText('命名回退')).toBeInTheDocument()
    expect(screen.queryByText('未命名章节')).not.toBeInTheDocument()
  })

  test('keeps a chapter without an id renderable', () => {
    renderExpanded([{ title: '无 ID 的章节' }])

    expect(screen.getByText('无 ID 的章节')).toBeInTheDocument()
  })
})
