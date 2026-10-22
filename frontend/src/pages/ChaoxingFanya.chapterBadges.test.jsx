import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

// The page uses the REAL CourseListSection here (the neighbouring test file
// mocks it), because this file is about how often chapters are refetched and
// what the expanded rows show.
const mocks = vi.hoisted(() => ({
  auth: {
    username: 'test-user',
    password: 'test-password',
    courses: [{ courseId: 'course-1', name: '测试课程' }],
    callApi: vi.fn(),
    setError: vi.fn(),
    setNotice: vi.fn(),
    loadCourses: vi.fn(),
    refreshLoginStatus: vi.fn().mockResolvedValue(false),
  },
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}))

vi.mock('../components', () => ({
  useToast: () => mocks.toast,
}))

vi.mock('./chaoxing-fanya/hooks/useAuthentication', () => {
  function useMockAuthentication() {
    const [courses, setCourses] = useState(mocks.auth.courses)
    mocks.auth.setCourses = setCourses
    return { ...mocks.auth, courses, setCourses }
  }

  return { default: useMockAuthentication }
})

vi.mock('./chaoxing-fanya/hooks/useTaskExecution', () => ({
  default: function MockTaskExecution() {
    const [taskId, setTaskId] = useState('')
    const [taskStatus, setTaskStatus] = useState(null)
    const [logs, setLogs] = useState([])
    const [taskHistory, setTaskHistory] = useState([])
    const logCursorRef = useRef(0)

    return {
      taskId, setTaskId, taskStatus, setTaskStatus,
      loading: false, setLoading: vi.fn(),
      logs, setLogs, logCursor: 0, setLogCursor: vi.fn(), logCursorRef,
      taskHistory, setTaskHistory,
      appendLogs: vi.fn(), controlTask: vi.fn(), selectTaskFromHistory: vi.fn(),
    }
  },
}))

vi.mock('./chaoxing-fanya/components/LoginSection', () => ({ default: () => null }))
vi.mock('./chaoxing-fanya/components/CoursePortalSection', () => ({ default: () => null }))
vi.mock('./chaoxing-fanya/components/ConfigSection', () => ({ default: () => null }))
vi.mock('./chaoxing-fanya/components/CourseQueueSection', () => ({ default: () => null }))
vi.mock('./chaoxing-fanya/components/TaskControlSection', () => ({ default: () => null }))
vi.mock('./chaoxing-fanya/components/TaskHistorySection', () => ({ default: () => null }))
vi.mock('./chaoxing-fanya/components/LogSection', () => ({ default: () => null }))

import ChaoxingFanya from './ChaoxingFanya'

const THE_FIRST_OPEN = [
  { id: 'p1', title: '1.1 函数', has_finished: true },
  { id: 'p2', title: '1.2 极限', has_finished: false, need_unlock: true },
]

const AFTER_RUNNING = [
  { id: 'p1', title: '1.1 函数', has_finished: true },
  { id: 'p2', title: '1.2 极限', has_finished: true },
]

const expandFirstCourse = async () => {
  const user = userEvent.setup()
  render(<ChaoxingFanya />)
  await user.click(screen.getByRole('button', { name: '章节' }))
  return user
}

describe('ChaoxingFanya chapter expansion', () => {
  beforeEach(() => {
    mocks.auth.callApi.mockReset()
    mocks.auth.setError.mockReset()
    mocks.auth.courses = [{ courseId: 'course-1', name: '测试课程' }]
  })

  afterEach(() => {
    cleanup()
  })

  test('shows completion and lock badges in the expanded chapter list', async () => {
    mocks.auth.callApi.mockResolvedValue({ chapters: THE_FIRST_OPEN })

    await expandFirstCourse()

    expect(await screen.findByText('1.1 函数')).toBeInTheDocument()
    expect(screen.getByText('已完成')).toBeInTheDocument()
    expect(screen.getByText('待解锁')).toBeInTheDocument()
  })

  test('refetches chapters every time the list is reopened', async () => {
    mocks.auth.callApi.mockResolvedValue({ chapters: THE_FIRST_OPEN })
    const user = await expandFirstCourse()

    await screen.findByText('1.1 函数')
    expect(mocks.auth.callApi).toHaveBeenCalledTimes(1)

    // Collapse, then come back after a task has flipped a chapter to 已完成.
    await user.click(screen.getByRole('button', { name: '收起' }))
    mocks.auth.callApi.mockResolvedValue({ chapters: AFTER_RUNNING })
    await user.click(screen.getByRole('button', { name: '章节' }))

    await waitFor(() => expect(mocks.auth.callApi).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getAllByText('已完成')).toHaveLength(2))
    // The stale 待解锁 badge must be gone once the backend says it finished.
    expect(screen.queryByText('待解锁')).not.toBeInTheDocument()
  })

  test('collapsing does not fire another request', async () => {
    mocks.auth.callApi.mockResolvedValue({ chapters: THE_FIRST_OPEN })
    const user = await expandFirstCourse()

    await screen.findByText('1.1 函数')
    await user.click(screen.getByRole('button', { name: '收起' }))

    expect(mocks.auth.callApi).toHaveBeenCalledTimes(1)
  })
})
