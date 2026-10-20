import { useState } from 'react'


export default function useTaskConfig() {


  const [speed, setSpeed] = useState(1)


  const [concurrency, setConcurrency] = useState(1)


  const [unopenedStrategy, setUnopenedStrategy] = useState('retry')


  // Ordered list of answer-bank providers; sent to the backend as a
  // comma-separated `provider` (单个=单题库，多个=按顺序回退). Default chain:
  // 言溪 first (best, if a token is set), then the free GO题库 — so 答题 still
  // works without a Token. (The shared answer cache is always consulted first,
  // so adding 本地缓存 as an extra link would be redundant.)
  // 默认只选言溪：配合下面预填的 Token，开箱即可答题。
  // GO 题库是免费兜底源，需要时用户可以自己勾上。
  const [tikuProvider, setTikuProvider] = useState(['TikuYanxi'])


  // Token 的两种状态：
  //   · ''        → 使用内置默认 Token（隐式生效，界面上只显示提示文字）
  //   · 非空字符串 → 用户自己填的，覆盖默认值
  //
  // 默认 token 不放进 state，而是在提交时由 resolveTikuToken() 兜底 ——
  // 这样它既不会出现在输入框里，也不会进入渲染树，只有发请求那一刻才出现。
  const [tikuToken, setTikuToken] = useState('')
  // 用户点过输入框、但还没输入任何内容。用来把占位提示从"使用默认 Token"
  // 换成真正的空，避免"看起来有值其实没有"的误解。
  const [tikuTokenTouched, setTikuTokenTouched] = useState(false)

  // AI / 题库适配器没有公网默认地址，必须显式提供才能通过后端校验。
  // 硅基流动有默认地址，只需上面的 key。
  const [aiEndpoint, setAiEndpoint] = useState('')
  const [aiModel, setAiModel] = useState('')
  // AI 的 Key 单独一个框：它和言溪的 Token 不是同一个东西，
  // 共用一个框会让「言溪 + AI 兜底」这个组合无法配置。
  const [aiApiKey, setAiApiKey] = useState('')
  // 硅基流动只有 Key 需要填（地址和模型有内置默认值）
  const [siliconFlowKey, setSiliconFlowKey] = useState('')
  const [adapterUrl, setAdapterUrl] = useState('')


  const [coverageThreshold, setCoverageThreshold] = useState(0.9)


  const [correctOptions, setCorrectOptions] = useState('对,正确,是')


  const [wrongOptions, setWrongOptions] = useState('错,错误,否')


  const [submitMode, setSubmitMode] = useState('submit')


  const [notifyService, setNotifyService] = useState('')


  const [notifyUrl, setNotifyUrl] = useState('')

  // Scheduling fields
  const [scheduleMode, setScheduleMode] = useState('now')
  const [scheduleStartAt, setScheduleStartAt] = useState('')
  const [scheduleStopAt, setScheduleStopAt] = useState('')
  const [startJitterMin, setStartJitterMin] = useState(10)
  const [stopJitterMin, setStopJitterMin] = useState(15)

  // Recurrence. 'once' keeps the one-shot datetime pickers; 'daily' /
  // 'every_other_day' switch to a wall-clock time plus a per-run duration cap.
  const [repeat, setRepeat] = useState('once')
  const [timeOfDay, setTimeOfDay] = useState('08:00')
  const [anchorDate, setAnchorDate] = useState('')
  const [maxDurationMin, setMaxDurationMin] = useState(120)


  return {
    speed, setSpeed,
    concurrency, setConcurrency,
    unopenedStrategy, setUnopenedStrategy,
    tikuProvider, setTikuProvider,
    tikuToken, setTikuToken,
    tikuTokenTouched, setTikuTokenTouched,
    aiEndpoint, setAiEndpoint,
    aiModel, setAiModel,
    aiApiKey, setAiApiKey,
    siliconFlowKey, setSiliconFlowKey,
    adapterUrl, setAdapterUrl,
    coverageThreshold, setCoverageThreshold,
    correctOptions, setCorrectOptions,
    wrongOptions, setWrongOptions,
    submitMode, setSubmitMode,
    notifyService, setNotifyService,
    notifyUrl, setNotifyUrl,
    scheduleMode, setScheduleMode,
    scheduleStartAt, setScheduleStartAt,
    scheduleStopAt, setScheduleStopAt,
    startJitterMin, setStartJitterMin,
    stopJitterMin, setStopJitterMin,
    repeat, setRepeat,
    timeOfDay, setTimeOfDay,
    anchorDate, setAnchorDate,
    maxDurationMin, setMaxDurationMin,
  }


}
