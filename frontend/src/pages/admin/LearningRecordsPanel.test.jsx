import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

vi.mock('../../utils/api', () => ({
  api: vi.fn(),
}))

// A STABLE toast object: returning a fresh one per render would change the
// `toast` identity, recreate `loadRecords`, and re-trigger its effect forever.
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))
vi.mock('../../components', async (importOriginal) => ({
  ...(await importOriginal()),
  useToast: () => toast,
}))

import { api } from '../../utils/api'
import LearningRecordsPanel from './LearningRecordsPanel'

const RECORD = {
  task_id: 'task-1',
  user_id: 'u1',
  username: 'demo',
  status: 'completed',
  message: 'Task completed',
  started_at: '2026-09-26T02:00:00+00:00',
  finished_at: '2026-09-26T03:30:00+00:00',
  updated_at: '2026-09-26T03:30:00+00:00',
  course_names: ['高等数学（上）', '大学英语综合'],
  progress: { completed: 2, total: 2, completed_chapters: 12, total_chapters: 12 },
}

const USERS = [
  { user_id: 'u1', username: 'demo' },
  { user_id: 'u2', username: 'alice' },
]

const respondWith = (records, pagination = {}) => {
  api.mockResolvedValue({
    status: 'success',
    data: records,
    pagination: { page: 1, page_size: 20, total: records.length, total_pages: 1, ...pagination },
  })
}

const renderPanel = () => render(<LearningRecordsPanel users={USERS} />)

// The status labels also appear as <option>s in the filter, so table assertions
// must be scoped to the table itself.
const table = () => within(screen.getByRole('table'))

describe('LearningRecordsPanel', () => {
  beforeEach(() => {
    api.mockReset()
    respondWith([RECORD])
  })

  afterEach(() => {
    cleanup()
  })

  test('renders one row per record with the required columns', async () => {
    renderPanel()

    await waitFor(() => expect(table().getByText('demo')).toBeInTheDocument())
    expect(table().getByText('高等数学（上）')).toBeInTheDocument()
    expect(table().getByText('已完成')).toBeInTheDocument()
    // Chapter granularity is what the spec asks for ("3/10 章").
    expect(table().getByText('12/12 章')).toBeInTheDocument()
  })

  test('shows every required column header', async () => {
    renderPanel()
    await waitFor(() => expect(table().getByText('demo')).toBeInTheDocument())

    for (const header of ['用户', '课程', '状态', '进度', '开始时间', '结束时间', '最后更新']) {
      expect(table().getByText(header)).toBeInTheDocument()
    }
  })

  test('falls back to the course tally when no chapter total is known', async () => {
    respondWith([
      { ...RECORD, progress: { completed: 1, total: 4, completed_chapters: 0, total_chapters: 0 } },
    ])
    renderPanel()

    await waitFor(() => expect(screen.getByText('1/4 门')).toBeInTheDocument())
  })

  test('marks an unfinished task as in progress instead of showing a blank end time', async () => {
    respondWith([{ ...RECORD, status: 'running', finished_at: null }])
    renderPanel()

    // The status column shows 进行中; the end-time column must say something
    // else, otherwise the row reads as if it has two statuses.
    await waitFor(() => expect(table().getByText('进行中')).toBeInTheDocument())
    expect(table().getByText('未结束')).toBeInTheDocument()
  })

  test('collapses a long course list into a +N badge', async () => {
    respondWith([
      { ...RECORD, course_names: ['课程一', '课程二', '课程三', '课程四'] },
    ])
    renderPanel()

    await waitFor(() => expect(table().getByText('+2')).toBeInTheDocument())
    expect(screen.queryByText('课程三')).not.toBeInTheDocument()
  })

  test('renders an empty state when there are no records', async () => {
    respondWith([])
    renderPanel()

    await waitFor(() => expect(screen.getByText('暂无刷课记录')).toBeInTheDocument())
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  test('passes the user filter to the backend and resets to page 1', async () => {
    const user = userEvent.setup()
    renderPanel()
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    api.mockClear()
    respondWith([RECORD])

    await user.selectOptions(screen.getByLabelText('按用户筛选'), 'u2')

    await waitFor(() => expect(api).toHaveBeenCalled())
    const url = String(api.mock.calls.at(-1)[0])
    expect(url).toContain('user_id=u2')
    expect(url).toContain('page=1')
  })

  test('passes the status filter to the backend', async () => {
    const user = userEvent.setup()
    renderPanel()
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    api.mockClear()
    respondWith([RECORD])

    await user.selectOptions(screen.getByLabelText('按状态筛选'), 'failed')

    await waitFor(() => expect(api).toHaveBeenCalled())
    expect(String(api.mock.calls.at(-1)[0])).toContain('status=failed')
  })

  test('widens a date filter to cover the whole selected day', async () => {
    const user = userEvent.setup()
    renderPanel()
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    api.mockClear()
    respondWith([RECORD])

    await user.type(screen.getByLabelText('起始日期'), '2026-09-26')

    await waitFor(() => expect(api).toHaveBeenCalled())
    const url = String(api.mock.calls.at(-1)[0])
    expect(url).toContain('since=')
    // Local midnight, not UTC midnight — the range must include that day.
    const decoded = decodeURIComponent(url)
    expect(decoded).toMatch(/since=\d{4}-\d{2}-\d{2}T/)
  })

  test('offers a clear-filters action only when a filter is active', async () => {
    const user = userEvent.setup()
    renderPanel()
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())

    expect(screen.queryByText('清除筛选')).not.toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('按状态筛选'), 'completed')

    await waitFor(() => expect(screen.getByText('清除筛选')).toBeInTheDocument())
  })

  test('paginates only when there is more than one page', async () => {
    respondWith([RECORD], { page: 1, total: 45, total_pages: 3 })
    renderPanel()

    await waitFor(() => expect(screen.getByText('第 1 / 3 页')).toBeInTheDocument())
    expect(screen.getByLabelText('上一页')).toBeDisabled()
    expect(screen.getByLabelText('下一页')).toBeEnabled()
  })

  test('hides pagination for a single page', async () => {
    renderPanel()
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())

    expect(screen.queryByText(/第 1 \/ 1 页/)).not.toBeInTheDocument()
  })

  test('requests the next page when 下一页 is clicked', async () => {
    respondWith([RECORD], { page: 1, total: 45, total_pages: 3 })
    const user = userEvent.setup()
    renderPanel()
    await waitFor(() => expect(screen.getByText('第 1 / 3 页')).toBeInTheDocument())
    api.mockClear()
    respondWith([RECORD], { page: 2, total: 45, total_pages: 3 })

    await user.click(screen.getByLabelText('下一页'))

    await waitFor(() => expect(api).toHaveBeenCalled())
    expect(String(api.mock.calls.at(-1)[0])).toContain('page=2')
  })

  test('shows a permission empty state on 403', async () => {
    const err = new Error('forbidden')
    err.status = 403
    api.mockRejectedValue(err)
    renderPanel()

    await waitFor(() => expect(screen.getByText('需要管理员权限')).toBeInTheDocument())
  })

  test('never renders the task payload (logs must not reach the table)', async () => {
    respondWith([{ ...RECORD, logs: [{ message: 'SECRET-LOG-LINE' }] }])
    const { container } = renderPanel()

    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    expect(container.textContent).not.toContain('SECRET-LOG-LINE')
  })
})
