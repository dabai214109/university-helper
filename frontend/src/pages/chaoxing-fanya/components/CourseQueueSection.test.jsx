import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'

import CourseQueueSection from './CourseQueueSection'

// getCourseId() builds "courseId_classId_cpi", so the queue keys must match
// that shape — the same shape the real page gets from /chaoxing/courses.
const COURSES = [
  { courseId: '111', classId: '1', courseName: '形势与政策' },
  { courseId: '222', classId: '2', courseName: '电力系统分析' },
  { courseId: '333', classId: '3', courseName: '控制工程 II' },
]
const ID = { a: '111_1', b: '222_2', c: '333_3' }

afterEach(cleanup)

describe('CourseQueueSection', () => {
  test('renders nothing when the queue is empty', () => {
    const { container } = render(
      <CourseQueueSection courses={COURSES} queuedCourses={[]} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  test('lists the queue in the order it will run', () => {
    render(
      <CourseQueueSection courses={COURSES} queuedCourses={[ID.c, ID.a, ID.b]} />
    )

    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(3)
    // The numbers are the execution order, and must follow the queued array,
    // not the course list order.
    expect(within(items[0]).getByText('1')).toBeInTheDocument()
    expect(within(items[0]).getByText('控制工程 II')).toBeInTheDocument()
    expect(within(items[1]).getByText('形势与政策')).toBeInTheDocument()
    expect(within(items[2]).getByText('电力系统分析')).toBeInTheDocument()
  })

  test('shows the course id alongside the name', () => {
    render(<CourseQueueSection courses={COURSES} queuedCourses={[ID.a]} />)
    expect(screen.getByText(ID.a)).toBeInTheDocument()
  })

  test('falls back to the id when the course is no longer in the list', () => {
    render(<CourseQueueSection courses={COURSES} queuedCourses={['999_9']} />)
    // Both the name slot and the id slot end up showing the id.
    expect(screen.getAllByText('999_9').length).toBeGreaterThan(0)
  })

  test('shows the count and a clear action', () => {
    const onClearQueue = vi.fn()
    render(
      <CourseQueueSection
        courses={COURSES}
        queuedCourses={[ID.a, ID.b]}
        onClearQueue={onClearQueue}
      />
    )

    expect(screen.getByText('2 门')).toBeInTheDocument()
    screen.getByRole('button', { name: '清空队列' }).click()
    expect(onClearQueue).toHaveBeenCalledTimes(1)
  })

  test('omits the clear button when no handler is given', () => {
    render(<CourseQueueSection courses={COURSES} queuedCourses={[ID.a]} />)
    expect(screen.queryByRole('button', { name: '清空队列' })).not.toBeInTheDocument()
  })
})
