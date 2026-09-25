import { Input } from '../../../components'
import ChaoxingQrLoginPanel from '../../chaoxing-shared/ChaoxingQrLoginPanel'
import { CARD } from '../utils'

export default function LoginSection({
  username,
  setUsername,
  password,
  setPassword,
  loginLoading,
  handleLogin,
  loginMethod,
  setLoginMethod,
  qrLogin,
}) {
  return (
    <section className={CARD}>
      <h2 className="mb-4 text-xl font-semibold text-text">登录账号</h2>

      <div
        role="radiogroup"
        aria-label="登录方式"
        className="mb-4 inline-flex rounded-full border border-border/60 bg-surface/70 p-0.5"
      >
        {[
          { value: 'qr', label: '扫码登录' },
          { value: 'password', label: '账号密码登录' },
        ].map((opt) => (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={loginMethod === opt.value}
            onClick={() => {
              if (opt.value === 'password') {
                void qrLogin.cancelQrLogin()
              }
              setLoginMethod(opt.value)
            }}
            className={`min-h-[40px] cursor-pointer rounded-full px-4 text-sm font-medium transition-colors ${
              loginMethod === opt.value
                ? 'bg-primary text-white'
                : 'text-text/70 hover:bg-surface-hover'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {loginMethod === 'qr' ? (
        <ChaoxingQrLoginPanel
          qrCode={qrLogin.qrCode}
          qrStatus={qrLogin.qrStatus}
          qrMessage={qrLogin.qrMessage}
          qrError={qrLogin.qrError}
          qrLoading={qrLogin.qrLoading}
          nickname={qrLogin.nickname}
          onRefresh={() => {
            void qrLogin.startQrLogin()
          }}
          hint="打开学习通 App，扫描二维码并在手机上确认，即可开始刷课。"
          buttonClassName="min-h-[44px] cursor-pointer rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white transition duration-200 hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
        />
      ) : (
        <form className="grid gap-4 md:grid-cols-2" onSubmit={handleLogin} noValidate>
          <Input
            id="fanya-username"
            label="超星账号"
            name="cx-username"
            autoComplete="username"
            disabled={loginLoading}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
          />

          <Input
            id="fanya-password"
            label="密码"
            type="password"
            name="cx-password"
            autoComplete="current-password"
            disabled={loginLoading}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />

          <button
            type="submit"
            disabled={loginLoading}
            aria-busy={loginLoading}
            className="min-h-[44px] cursor-pointer rounded-xl bg-primary px-6 py-3 font-medium text-white transition duration-200 hover:bg-primary/90 disabled:cursor-not-allowed disabled:bg-text-muted md:col-span-2"
          >
            {loginLoading ? '登录中...' : '登录并获取课程'}
          </button>
        </form>
      )}
    </section>
  )
}
