import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { RuntimeProfileContext } from '../components/runtimeProfileContext'
import { ToastProvider } from '../components/Toast'
import { api } from '../utils/api'
import Zhihuishu from './Zhihuishu'

vi.mock('../utils/api', () => ({
  api: vi.fn(),
}))

const runtimeProfile = {
  profile: 'local',
  isLocal: true,
  requiresAuth: false,
  loading: false,
}

// The course-detail endpoint describes chapters; the video endpoint is the one
// that carries watch_state. Completion must come from the latter.
const COURSE_DETAIL = {
  courseId: 'course-1',
  name: '管理学原理',
  chapters: [
    {
      chapterId: 'ch1',
      chapterName: '第一章 绪论',
      videoLearningDtos: [{ videoId: 'v1' }, { videoId: 'v2' }],
    },
    {
      chapterId: 'ch2',
      chapterName: '第二章 计划',
      videoLearningDtos: [{ videoId: 'v3' }, { videoId: 'v4' }],
    },
  ],
}

// Two of four videos are already watched: v1 (status completed) and v3
// (watch_state 1 only, which is how the platform reports it). The videos
// endpoint groups them by chapter_title.
const VIDEOS = [
  { id: 'v1', name: '1.1 什么是管理学', status: 'completed', watch_state: 1, chapter_title: '第一章 绪论' },
  { id: 'v2', name: '1.2 管理的职能', status: 'pending', watch_state: 0, chapter_title: '第一章 绪论' },
  { id: 'v3', name: '2.1 目标与计划', status: 'pending', watch_state: 1, chapter_title: '第二章 计划' },
  { id: 'v4', name: '2.2 决策方法', status: 'pending', watch_state: 0, chapter_title: '第二章 计划' },
]

const createApiMock = () => {
  api.mockImplementation((path) => {
    if (path === '/course/zhihuishu/config') return Promise.resolve({ data: {} })
    if (path === '/course/zhihuishu/status') return Promise.resolve({ data: { logged_in: true } })
    if (path === '/course/zhihuishu/courses/grouped') {
      return Promise.resolve({ data: [{ group: '本学期', courses: [{ courseId: 'course-1', name: '管理学原理' }] }] })
    }
    if (path === '/course/zhihuishu/tasks') return Promise.resolve({ data: [] })
    if (path === '/course/zhihuishu/courses/course-1') return Promise.resolve({ data: COURSE_DETAIL })
    if (path === '/course/zhihuishu/videos/course-1') return Promise.resolve({ data: VIDEOS })
    if (path === '/course/zhihuishu/courses/course-1/videos') return Promise.resolve({ data: VIDEOS })
    return Promise.resolve({ data: {} })
  })
}

const renderPage = () => render(
  <RuntimeProfileContext.Provider value={runtimeProfile}>
    <ToastProvider>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Zhihuishu />
      </MemoryRouter>
    </ToastProvider>
  </RuntimeProfileContext.Provider>,
)

const flushPromises = async () => {
  await act(async () => {
    for (let index = 0; index < 20; index += 1) {
      await Promise.resolve()
    }
  })
}

describe('Zhihuishu chapter completion counts', () => {
  beforeEach(() => {
    localStorage.clear()
    api.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  test('counts watched videos per chapter from watch_state', async () => {
    createApiMock()
    renderPage()
    await flushPromises()

    // The page opens on the 登录 tab; the structure panel lives under 课程.
    const coursesTab = await screen.findByRole('tab', { name: '课程' })
    await act(async () => {
      fireEvent.click(coursesTab)
    })
    await flushPromises()

    const select = await screen.findByLabelText('分组课程')
    await act(async () => {
      fireEvent.change(select, { target: { value: 'course-1' } })
    })
    await flushPromises()

    // chapter 1: v1 watched (status completed) of 2
    // chapter 2: v3 watched (watch_state 1 while status stays pending) of 2
    const chapterOne = await screen.findByText('第一章 绪论')
    expect(chapterOne).toBeInTheDocument()
    const panel = chapterOne.closest('li')
    expect(panel.textContent).toContain('视频数：2')
    expect(panel.textContent).toContain('已看 1/2')

    const chapterTwo = await screen.findByText('第二章 计划')
    const panelTwo = chapterTwo.closest('li')
    // This is the case the backend fix enables: status was never 'completed',
    // so only watch_state can tell us it was watched.
    expect(panelTwo.textContent).toContain('已看 1/2')
  })
})
