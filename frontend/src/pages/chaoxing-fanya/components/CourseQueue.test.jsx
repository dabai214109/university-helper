import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test } from 'vitest'

import CourseQueue from './CourseQueue'

const renderQueue = (courses) => render(<CourseQueue courses={courses} />)

describe('CourseQueue', () => {
  afterEach(() => {
    cleanup()
  })

  test('renders nothing when there is no queue yet', () => {
    const { container } = render(<CourseQueue courses={undefined} />)

    expect(container).toBeEmptyDOMElement()
  })

  test('lists every course in the given order, not the completion order', () => {
    renderQueue([
      { name: '线性代数', status: 'pending' },
      { name: '高等数学', status: 'running' },
      { name: '大学英语', status: 'completed' },
    ])

    const items = screen.getAllByRole('listitem')
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining('线性代数'),
      expect.stringContaining('高等数学'),
      expect.stringContaining('大学英语'),
    ])
    // Position numbers reflect the FIFO order the user confirmed.
    expect(screen.getByText('1')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  test('shows per-course chapter progress when the backend reports it', () => {
    renderQueue([{ name: '高等数学', status: 'running', chapters_done: 3, chapters_total: 10 }])

    expect(screen.getByText('3/10 章')).toBeInTheDocument()
    expect(screen.getByText('进行中')).toBeInTheDocument()
  })

  test('hides the chapter counter until a total is known', () => {
    renderQueue([{ name: '高等数学', status: 'pending', chapters_done: 0, chapters_total: 0 }])

    expect(screen.queryByText(/章$/)).not.toBeInTheDocument()
    expect(screen.getByText('等待中')).toBeInTheDocument()
  })

  test('summarises completed against total', () => {
    renderQueue([
      { name: 'A', status: 'completed' },
      { name: 'B', status: 'completed' },
      { name: 'C', status: 'running' },
    ])

    expect(screen.getByText('2/3')).toBeInTheDocument()
  })

  test('labels a cancelled course distinctly from a failed one', () => {
    renderQueue([
      { name: 'A', status: 'failed' },
      { name: 'B', status: 'cancelled' },
    ])

    expect(screen.getByText('失败')).toBeInTheDocument()
    expect(screen.getByText('已取消')).toBeInTheDocument()
  })

  test('falls back to a placeholder for an unknown status', () => {
    renderQueue([{ name: 'A', status: 'something-new' }])

    expect(screen.getByText('等待中')).toBeInTheDocument()
  })
})
