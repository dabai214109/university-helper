import { Loader2, QrCode, RefreshCcw } from 'lucide-react'

const QR_STATUS_LABELS = {
  idle: '未开始',
  loading: '加载中',
  pending: '等待扫码',
  scanned: '已扫码，待确认',
  success: '登录成功',
  failed: '登录失败',
}

/**
 * QR-login panel shared by the sign-in and Fanya pages.
 *
 * The pages differ in styling (they predate a shared card class), so the
 * presentational shell is passed in and only the QR behaviour is shared.
 */
export default function ChaoxingQrLoginPanel({
  qrCode,
  qrStatus,
  qrMessage,
  qrError,
  qrLoading,
  nickname,
  onRefresh,
  className = '',
  imageClassName = '',
  buttonClassName = '',
  title = '学习通扫码登录',
  hint = '打开学习通 App，扫描二维码并在手机上确认登录。',
}) {
  const statusLabel = QR_STATUS_LABELS[qrStatus] || qrStatus || '未知'

  return (
    <div className={`space-y-4 ${className}`}>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-text">{title}</h2>
        <button
          type="button"
          onClick={onRefresh}
          disabled={qrLoading}
          className={buttonClassName}
        >
          <span className="inline-flex items-center gap-2">
            <RefreshCcw className="h-4 w-4" aria-hidden="true" />
            {qrLoading ? '生成中...' : qrCode ? '刷新二维码' : '生成二维码'}
          </span>
        </button>
      </div>

      <p className="text-sm text-text/70">{qrMessage || hint}</p>

      <div className="flex justify-center">
        {qrLoading && !qrCode && (
          <div className="flex h-56 w-56 items-center justify-center rounded-2xl border border-border/30 bg-surface/70">
            <Loader2 className="h-8 w-8 animate-spin text-primary" aria-hidden="true" />
          </div>
        )}
        {!qrLoading && qrCode && (
          <img
            src={`data:image/png;base64,${qrCode}`}
            alt="学习通登录二维码"
            className={`h-56 w-56 rounded-2xl border border-border/30 bg-surface object-contain shadow-sm ${imageClassName}`}
          />
        )}
        {!qrLoading && !qrCode && (
          <div className="flex h-56 w-56 items-center justify-center rounded-2xl border border-dashed border-border/40 bg-surface/60 text-text/50">
            <QrCode className="h-10 w-10" aria-hidden="true" />
          </div>
        )}
      </div>

      <div className="rounded-xl border border-border/30 bg-surface/70 p-3 text-sm text-text">
        <p className="flex items-center gap-2">
          <QrCode className="h-4 w-4 text-primary" aria-hidden="true" />
          状态：{statusLabel}
          {nickname ? `（${nickname}）` : ''}
        </p>
        {qrError && <p className="mt-2 text-danger">{qrError}</p>}
      </div>
    </div>
  )
}
