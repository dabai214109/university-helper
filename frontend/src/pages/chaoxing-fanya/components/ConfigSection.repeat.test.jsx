import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, test, vi } from 'vitest'

import ConfigSection from './ConfigSection'

const baseProps = {
  speed: 1.5,
  setSpeed: vi.fn(),
  concurrency: 4,
  setConcurrency: vi.fn(),
  unopenedStrategy: 'retry',
  setUnopenedStrategy: vi.fn(),
  tikuProvider: ['TikuYanxi'],
  setTikuProvider: vi.fn(),
  tikuToken: '',
  setTikuToken: vi.fn(),
  coverageThreshold: 0.9,
  setCoverageThreshold: vi.fn(),
  correctOptions: '对,正确,是',
  setCorrectOptions: vi.fn(),
  wrongOptions: '错,错误,否',
  setWrongOptions: vi.fn(),
  submitMode: 'submit',
  setSubmitMode: vi.fn(),
  notifyService: '',
  setNotifyService: vi.fn(),
  notifyUrl: '',
  setNotifyUrl: vi.fn(),
  scheduleMode: 'scheduled',
  setScheduleMode: vi.fn(),
  scheduleStartAt: '',
  setScheduleStartAt: vi.fn(),
  scheduleStopAt: '',
  setScheduleStopAt: vi.fn(),
  startJitterMin: 10,
  setStartJitterMin: vi.fn(),
  stopJitterMin: 15,
  setStopJitterMin: vi.fn(),
  repeat: 'once',
  setRepeat: vi.fn(),
  timeOfDay: '08:00',
  setTimeOfDay: vi.fn(),
  anchorDate: '',
  setAnchorDate: vi.fn(),
  maxDurationMin: 120,
  setMaxDurationMin: vi.fn(),
}

const renderConfig = (overrides = {}) => render(<ConfigSection {...baseProps} {...overrides} />)

describe('ConfigSection execution cadence', () => {
  afterEach(() => {
    cleanup()
  })

  test('offers all three cadences when scheduled', () => {
    renderConfig()

    expect(screen.getByRole('button', { name: /仅一次/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /每天/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /隔天/ })).toBeInTheDocument()
  })

  test('hides the cadence picker entirely for immediate runs', () => {
    renderConfig({ scheduleMode: 'now' })

    expect(screen.queryByRole('button', { name: /每天/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/启动时间/)).not.toBeInTheDocument()
  })

  test('one-shot mode keeps the absolute datetime pickers', () => {
    renderConfig({ repeat: 'once' })

    expect(screen.getByLabelText(/启动时间/)).toBeInTheDocument()
    expect(screen.getByLabelText(/停止时间/)).toBeInTheDocument()
    // The wall-clock picker must not be present in one-shot mode.
    expect(screen.queryByLabelText(/执行时间/)).not.toBeInTheDocument()
  })

  test('daily mode swaps in a time picker and a duration cap', () => {
    renderConfig({ repeat: 'daily' })

    expect(screen.getByLabelText(/执行时间/)).toBeInTheDocument()
    expect(screen.getByLabelText(/单次最长运行/)).toBeInTheDocument()
    // The absolute pickers are replaced, not merely supplemented.
    expect(screen.queryByLabelText(/启动时间/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/停止时间/)).not.toBeInTheDocument()
  })

  test('every-other-day mode additionally asks for an anchor date', () => {
    renderConfig({ repeat: 'every_other_day' })

    expect(screen.getByLabelText(/隔天基准日期/)).toBeInTheDocument()
  })

  test('daily mode does not ask for an anchor date', () => {
    renderConfig({ repeat: 'daily' })

    expect(screen.queryByLabelText(/隔天基准日期/)).not.toBeInTheDocument()
  })

  test('jitter sliders survive in every cadence', () => {
    renderConfig({ repeat: 'daily' })

    expect(screen.getByLabelText(/启动波动/)).toBeInTheDocument()
    expect(screen.getByLabelText(/停止波动/)).toBeInTheDocument()
  })

  test('explains that a recurring run produces one record per firing', () => {
    renderConfig({ repeat: 'daily' })

    expect(screen.getByText(/每次到点生成一条独立的刷课记录/)).toBeInTheDocument()
  })

  test('selecting a cadence reports it to the parent', async () => {
    const setRepeat = vi.fn()
    const user = userEvent.setup()
    renderConfig({ setRepeat })

    await user.click(screen.getByRole('button', { name: /每天/ }))

    expect(setRepeat).toHaveBeenCalledWith('daily')
  })

  test('硅基流动选中时出现独立的 Key 输入框', () => {
    renderConfig({ tikuProvider: ['SiliconFlow'] })

    expect(screen.getByLabelText(/硅基流动 API Key/)).toBeInTheDocument()
    // 硅基流动不需要地址和模型（有内置默认值）
    expect(screen.queryByLabelText(/AI 接口地址/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/AI 模型名/)).not.toBeInTheDocument()
  })

  test('选中 AI 时出现地址/模型/Key 三个框，但不出现硅基流动的框', () => {
    renderConfig({ tikuProvider: ['AI'] })

    expect(screen.getByLabelText(/AI 接口地址/)).toBeInTheDocument()
    expect(screen.getByLabelText(/AI 模型名/)).toBeInTheDocument()
    expect(screen.getByLabelText(/AI API Key/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/硅基流动 API Key/)).not.toBeInTheDocument()
  })

  test('同时选 AI 和硅基流动时两组输入框都在', () => {
    renderConfig({ tikuProvider: ['AI', 'SiliconFlow'] })

    expect(screen.getByLabelText(/AI 接口地址/)).toBeInTheDocument()
    expect(screen.getByLabelText(/AI API Key/)).toBeInTheDocument()
    expect(screen.getByLabelText(/硅基流动 API Key/)).toBeInTheDocument()
  })

  test('都不选时没有任何密钥输入框', () => {
    renderConfig({ tikuProvider: ['TikuGo'] })

    expect(screen.queryByLabelText(/AI 接口地址/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/AI API Key/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/硅基流动 API Key/)).not.toBeInTheDocument()
    // 但题库 Token 框始终在
    expect(screen.getByLabelText(/题库 Token/)).toBeInTheDocument()
  })

  test('Token 输入框默认为空，并提示使用默认 Token', () => {
    renderConfig({ tikuToken: '' })

    const input = screen.getByLabelText(/题库 Token/)
    // Empty, not prefilled: the built-in token never enters the DOM.
    expect(input).toHaveValue('')
    expect(input).toHaveAttribute('type', 'password')
    expect(screen.getByPlaceholderText(/使用默认 Token/)).toBeInTheDocument()
    expect(screen.getByText(/默认 Token 已内置并自动生效/)).toBeInTheDocument()
  })

  test('开始输入后提示切换为"正在使用你输入的"', () => {
    renderConfig({ tikuToken: 'my-own-token' })
    expect(screen.getByText(/正在使用你输入的 Token/)).toBeInTheDocument()
  })

  test('没有复制按钮，也没有查看按钮（默认 Token 完全不可见）', () => {
    renderConfig({ tikuToken: '' })

    expect(screen.queryByRole('button', { name: '复制言溪 Token' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /显示 Token|隐藏 Token/ })).not.toBeInTheDocument()
  })

  test('选了言溪时提示"使用默认 Token"', () => {
    renderConfig({ tikuProvider: ['TikuYanxi'], tikuToken: '' })
    expect(screen.getByPlaceholderText(/使用默认 Token/)).toBeInTheDocument()
    expect(screen.getByText(/默认 Token 已内置并自动生效/)).toBeInTheDocument()
  })

  test('没选言溪时不显示"使用默认 Token"，并说明不生效', () => {
    renderConfig({ tikuProvider: ['TikuGo'], tikuToken: '' })

    // The built-in token belongs to 言溪 only; claiming it is in use while
    // another provider is selected would be a lie.
    expect(screen.queryByPlaceholderText(/使用默认 Token/)).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/未选择言溪题库/)).toBeInTheDocument()
    expect(screen.getByText(/只给「言溪题库」用；当前没有选中它/)).toBeInTheDocument()
  })

  test('选了 Like 题库也不显示默认 Token 提示', () => {
    renderConfig({ tikuProvider: ['TikuLike'], tikuToken: '' })
    expect(screen.queryByPlaceholderText(/使用默认 Token/)).not.toBeInTheDocument()
    expect(screen.getByText(/当前没有选中它/)).toBeInTheDocument()
  })

  test('言溪和其他题库一起选时，默认 Token 仍然生效', () => {
    renderConfig({ tikuProvider: ['TikuYanxi', 'TikuGo'], tikuToken: '' })
    expect(screen.getByPlaceholderText(/使用默认 Token/)).toBeInTheDocument()
  })

  test('用户自己填的 Token 在没选言溪时也会照常发送', () => {
    renderConfig({ tikuProvider: ['TikuLike'], tikuToken: 'my-token' })
    // 输入了具体值就不该被"未选言溪"的提示干扰
    expect(screen.getByText(/正在使用你输入的 Token/)).toBeInTheDocument()
  })
})
