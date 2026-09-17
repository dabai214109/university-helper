import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { BookOpen, Eye, EyeOff } from 'lucide-react'
import { Button, Card, Input, ThemeToggle, useRuntimeProfile } from '../components'
import { api, LOCAL_PROFILE_AUTH_CODE } from '../utils/api'
import { setToken } from '../utils/auth'

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export default function Login() {
  const [form, setForm] = useState({ email: '', password: '' })
  const [error, setError] = useState('')
  const [fieldErrors, setFieldErrors] = useState({})
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const navigate = useNavigate()
  const { markLocal } = useRuntimeProfile()
  const location = useLocation()
  const from = location.state?.from || '/dashboard'

  const handleSubmit = async (event) => {
    event.preventDefault()
    if (submitting) return

    const nextErrors = {}
    const email = form.email.trim()
    if (!email) nextErrors.email = '请输入邮箱。'
    else if (!EMAIL_PATTERN.test(email)) nextErrors.email = '请输入有效的邮箱地址。'
    if (!form.password) nextErrors.password = '请输入密码。'
    setFieldErrors(nextErrors)
    setError('')
    if (Object.keys(nextErrors).length > 0) return

    setSubmitting(true)
    try {
      const response = await api('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ ...form, email }),
      })
      setToken(response.access_token || response.token, response.shuake_token)
      navigate(from, { replace: true })
    } catch (requestError) {
      if (requestError?.payload?.code === LOCAL_PROFILE_AUTH_CODE) {
        markLocal()
        navigate('/dashboard', { replace: true })
        return
      }
      setError(requestError.message || '登录失败，请检查邮箱和密码。')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center bg-background px-4 py-20 sm:px-8">
      <div className="absolute right-4 top-4 z-20"><ThemeToggle /></div>
      <section className="w-full max-w-md">
        <Card padding="spacious" tone="elevated">
          <Link to="/login" className="mb-7 flex w-fit items-center gap-3" aria-label="学道登录页">
            <span className="relative grid h-11 w-11 place-items-center overflow-hidden rounded-xl bg-secondary text-background dark:text-text">
              <BookOpen className="h-5 w-5" aria-hidden="true" />
              <span className="absolute bottom-0 right-0 h-2.5 w-2.5 bg-cta" aria-hidden="true" />
            </span>
            <span className="text-xl font-black tracking-[0.18em]">学道</span>
          </Link>

          <h1 className="text-3xl font-black tracking-tight text-text">登录</h1>

          <form onSubmit={handleSubmit} className="mt-7 space-y-5" noValidate>
            <Input
              id="login-email"
              label="邮箱"
              type="email"
              autoComplete="email"
              inputMode="email"
              value={form.email}
              onChange={(event) => {
                setForm((previous) => ({ ...previous, email: event.target.value }))
                setFieldErrors((previous) => ({ ...previous, email: '' }))
              }}
              error={fieldErrors.email}
              required
            />
            <Input
              id="login-password"
              label="密码"
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              value={form.password}
              onChange={(event) => {
                setForm((previous) => ({ ...previous, password: event.target.value }))
                setFieldErrors((previous) => ({ ...previous, password: '' }))
              }}
              error={fieldErrors.password}
              trailing={(
                <button
                  type="button"
                  onClick={() => setShowPassword((visible) => !visible)}
                  aria-label={showPassword ? '隐藏密码' : '显示密码'}
                  className="grid h-10 w-10 place-items-center rounded-lg text-text-muted hover:bg-surface-hover hover:text-text focus-visible:ring-offset-0"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" aria-hidden="true" /> : <Eye className="h-4 w-4" aria-hidden="true" />}
                </button>
              )}
              required
            />
            {error && <p role="alert" className="rounded-xl border border-danger/30 bg-danger-surface px-3 py-2.5 text-sm text-danger">{error}</p>}
            <Button type="submit" variant="cta" size="lg" className="w-full gap-2" loading={submitting} loadingLabel="正在登录…">
              登录学道
            </Button>
          </form>

          <p className="mt-6 text-center text-sm text-text-muted">
            还没有账号？{' '}
            <Link to="/register" className="font-bold text-primary hover:underline">创建账号</Link>
          </p>
        </Card>
      </section>
    </main>
  )
}
