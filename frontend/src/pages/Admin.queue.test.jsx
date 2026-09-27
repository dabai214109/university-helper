import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

// The admin page pulls in the toast context and the API client; neither is
// relevant to how a queue card renders. The toast object must be STABLE —
// returning a fresh one per render changes `handleApiError`'s identity, which
// recreates every loader and re-fires the load effect without end.
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))
vi.mock('../utils/api', () => ({ api: vi.fn() }))
vi.mock('../components', async (importOriginal) => ({
  ...(await importOriginal()),
  useToast: () => toast,
}))

import { api } from '../utils/api'
import Admin from './Admin'

const oneShot = {
  task_id: 'one-shot-1',
  user_id: 'u1',
  username: 'demo',
  status: 'scheduled',
  schedule: {
    repeat: 'once',
    start_at: '2026-09-27T08:00:00+00:00',
    stop_at: null,
    fire_at: '2026-09-27T08:03:00+00:00',
    start_jitter_min: 10,
    stop_jitter_min: 0,
  },
}

const recurring = {
  task_id: 'tpl-1',
  user_id: 'u1',
  username: 'demo',
  status: 'recurring',
  schedule: {
    repeat: 'daily',
    time_of_day: '08:00',
    next_fire_at: '2026-09-27T08:00:00+00:00',
    fired_count: 7,
    max_duration_min: 120,
    start_jitter_min: 10,
    start_at: null,
    stop_at: null,
    fire_at: null,
  },
}

// Admin loads through the `api` client, which prefixes /api/v1 itself.
const routeApi = (scheduledRows, recurringRows) => {
  api.mockImplementation(async (path) => {
    const target = String(path)
    if (target.includes('status=recurring')) return { status: 'success', data: recurringRows }
    if (target.includes('status=scheduled')) return { status: 'success', data: scheduledRows }
    if (target.includes('/admin/tasks')) {
      return { status: 'success', data: [...scheduledRows, ...recurringRows] }
    }
    return { status: 'success', data: [] }
  })
}

const openQueueTab = async () => {
  const queueTab = await screen.findByRole('tab', { name: /定时队列/ })
  queueTab.click()
}

describe('Admin queue cards', () => {
  beforeEach(() => {
    api.mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  test('a one-shot task shows its planned and actual start times', async () => {
    routeApi([oneShot], [])
    render(<Admin />)
    await openQueueTab()

    expect(await screen.findByText('计划启动')).toBeInTheDocument()
    expect(screen.getByText('实际启动')).toBeInTheDocument()
    // A one-shot task is not a recurring one.
    expect(screen.queryByText('执行周期')).not.toBeInTheDocument()
  })

  test('a recurring template shows its rule instead of a start time', async () => {
    routeApi([], [recurring])
    render(<Admin />)
    await openQueueTab()

    // The rule, the next firing and the run count — not start_at/fire_at, which
    // a recurring template does not have.
    expect(await screen.findByText('执行周期')).toBeInTheDocument()
    expect(screen.getByText('下次执行')).toBeInTheDocument()
    expect(screen.getByText('已执行')).toBeInTheDocument()
    expect(screen.queryByText('计划启动')).not.toBeInTheDocument()
    expect(screen.queryByText('实际启动')).not.toBeInTheDocument()
  })

  test('a recurring card is badged as recurring, not as scheduled', async () => {
    routeApi([], [recurring])
    render(<Admin />)
    await openQueueTab()

    await waitFor(() => expect(screen.getByText('周期任务')).toBeInTheDocument())
    expect(screen.queryByText('定时中')).not.toBeInTheDocument()
  })

  test('a recurring card offers to cancel the schedule', async () => {
    routeApi([], [recurring])
    render(<Admin />)
    await openQueueTab()

    expect(await screen.findByRole('button', { name: /取消周期/ })).toBeInTheDocument()
  })

  test('shows the run count and per-run cap for a recurring template', async () => {
    routeApi([], [recurring])
    render(<Admin />)
    await openQueueTab()

    expect(await screen.findByText(/7 次/)).toBeInTheDocument()
    expect(screen.getByText(/单次上限 120 分钟/)).toBeInTheDocument()
  })

  test('a one-shot card still offers a plain cancel', async () => {
    routeApi([oneShot], [])
    render(<Admin />)
    await openQueueTab()

    expect(await screen.findByRole('button', { name: /^取消$/ })).toBeInTheDocument()
  })
})
