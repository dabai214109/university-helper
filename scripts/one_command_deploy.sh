#!/usr/bin/env bash
# 一条命令完成部署 / 更新，保留全部数据。
#
#   bash scripts/one_command_deploy.sh            # 首次部署或更新（自动判断）
#   bash scripts/one_command_deploy.sh --backup-only
#   bash scripts/one_command_deploy.sh --status
#
# 设计要点：
#   · 永远不使用 `docker compose down -v`（-v 会删数据卷）
#   · 从不重新生成 .env 里已有的密钥（换了 CREDENTIAL_ENCRYPTION_KEY 会让
#     数据库里已存的平台密码永久解不开）
#   · 更新前自动备份 .env 和数据库
#   · 能识别「旧的上游 ghcr.io 镜像部署」，并在它占着端口/容器名时给出明确指引
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1
PROJECT_DIR="$(pwd)"

COMPOSE_FILE="docker-compose.yml"
PROJECT="${COMPOSE_PROJECT_NAME:-university-helper}"
DB_CONTAINER="shuake-easy-learning-db"
APP_CONTAINER="shuake-easy-learning-app"
WEB_CONTAINER="shuake-easy-learning-web"
BACKUP_DIR="${UH_BACKUP_DIR:-/www/backup}"

# 上游镜像前缀。用它区分「上游预构建镜像」和「本 fork 本地构建」。
UPSTREAM_IMAGE_PREFIX="ghcr.io/sweetcornna/"

info() { printf '\033[36m[i]\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m[✓]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[✗]\033[0m %s\n' "$*" >&2; exit 1; }

dc() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" "$@"; }

# ---------------------------------------------------------------- 环境检查
need_docker() {
  command -v docker >/dev/null 2>&1 || die "未安装 Docker。宝塔面板 → 软件商店 → 安装 Docker"
  docker compose version >/dev/null 2>&1 || die "需要 Docker Compose v2（docker compose，不是 docker-compose）"
  docker info >/dev/null 2>&1 || die "Docker 守护进程不可达（未启动，或当前用户无权限）"
}

# ---------------------------------------------------------------- 旧部署识别
# 上游部署的镜像是 ghcr.io/sweetcornna/*；本 fork 是本地构建的
# university-helper-app:fork。容器名两者相同（历史原因），所以必须看镜像。
# 判断镜像到底是「从 GHCR 拉下来的上游镜像」还是「本地构建的」。
#
# 关键点：**镜像名不足以判断来源**。上游的 docker-compose.release.yml 里写的是
#   image: ghcr.io/sweetcornna/university-helper-app:latest
# 但用 `docker compose build` 本地构建时，产出的镜像**也叫这个名字**。
# 所以只看名字会把本地构建误判成「上游镜像」。
#
# 可靠依据是 RepoDigests：
#   · docker pull 下来的镜像 → 有 RepoDigests（repo@sha256:...），因为它来自 registry
#   · 本地 docker build 的镜像 → RepoDigests 为空，它从未进过 registry
detect_existing() {
  local image digests
  image="$(docker inspect "$APP_CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || true)"
  if [[ -z "$image" ]]; then
    echo "none"
    return
  fi

  # RepoDigests 非空 = 从 registry 拉取的镜像
  digests="$(docker inspect "$APP_CONTAINER" --format '{{json .RepoDigests}}' 2>/dev/null || echo '[]')"
  if [[ "$digests" == "[]" || -z "$digests" ]]; then
    # 没有 digest：本地构建产物。名字即使是 ghcr.io/... 也无关紧要。
    echo "fork"
    return
  fi

  if [[ "$image" == ${UPSTREAM_IMAGE_PREFIX}* ]]; then
    echo "upstream"
  else
    echo "fork"
  fi
}

report_legacy() {
  local image="$1"
  cat >&2 <<EOF

$(printf '\033[33m[!]\033[0m') 检测到旧的「上游预构建镜像」部署：

    容器： $APP_CONTAINER
    镜像： $image

    这是上游 sweetcornna/university-helper 的 GHCR 镜像 —— 它**不包含**本 fork 的
    定时任务、管理面板、扫码登录、周期任务等功能。

沿用现有数据卷继续部署即可（数据不会丢），但必须先停掉旧容器，否则容器名冲突。
下面的流程会自动处理，你只需要确认继续。

EOF
}

# ---------------------------------------------------------------- 密钥
ensure_env() {
  if [[ ! -f .env ]]; then
    [[ -f .env.example ]] || die "缺少 .env.example，源码不完整"
    info "未发现 .env，从 .env.example 生成并填入随机密钥"
    cp .env.example .env
    chmod 600 .env

    local pg secret fernet
    pg="$(openssl rand -hex 24)"
    secret="$(openssl rand -hex 32)"
    fernet="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"

    # 就地替换，保留文件 inode 与权限
    python3 - "$pg" "$secret" "$fernet" <<'PY'
import re, sys
pg, secret, fernet = sys.argv[1:4]
path = ".env"
text = open(path, encoding="utf-8").read()
def setkey(text, key, value):
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        return re.sub(rf"^{key}=.*$", f"{key}={value}", text, count=1, flags=re.M)
    if not text.endswith("\n"):
        text += "\n"
    return text + f"{key}={value}\n"
text = setkey(text, "POSTGRES_PASSWORD", pg)
text = setkey(text, "SECRET_KEY", secret)
text = setkey(text, "CREDENTIAL_ENCRYPTION_KEY", fernet)
text = setkey(text, "UPDATE_CHECK_ENABLED", "false")
open(path, "w", encoding="utf-8").write(text)
PY
    ok ".env 已生成（含随机密钥）"
  else
    ok ".env 已存在，保留现有密钥（不会重新生成）"
  fi

  # 缺了这两个就解不开数据，必须拦住
  grep -qE '^CREDENTIAL_ENCRYPTION_KEY=.+' .env \
    || die "CREDENTIAL_ENCRYPTION_KEY 为空。若这是旧部署，请从 .env 备份恢复 —— 新生成的密钥解不开已存的平台密码。"
  grep -qE '^SECRET_KEY=.+' .env || die "SECRET_KEY 为空，请从备份恢复"
  grep -qE '^POSTGRES_PASSWORD=.+' .env || die "POSTGRES_PASSWORD 为空，请从备份恢复"

  # 本 fork 必须关闭更新检查（版本号与上游一致，否则一直提示升级到上游）
  if ! grep -qE '^UPDATE_CHECK_ENABLED=' .env; then
    printf '\nUPDATE_CHECK_ENABLED=false\n' >> .env
    info "已追加 UPDATE_CHECK_ENABLED=false"
  fi

  # CORS 默认只放行 localhost，用 IP 访问会被浏览器拦掉。
  # 首次部署时自动补上本机所有 IPv4 + 实际端口。
  local cors
  cors="$(grep -E '^CORS_ORIGINS=' .env | tail -1 | cut -d= -f2-)"
  if [[ -z "$cors" || "$cors" == *localhost* ]]; then
    local ips port json
    port="$(grep -E '^HTTP_PORT=' .env | tail -1 | cut -d= -f2)"
    port="${port:-8080}"
    ips="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' || true)"
    json='["http://localhost:'"$port"'","http://127.0.0.1:'"$port"'"'
    for ip in $ips; do
      json+=',"http://'"$ip"':'"$port"'"'
    done
    json+=']'
    if grep -qE '^CORS_ORIGINS=' .env; then
      python3 - "$json" <<'PY'
import re, sys
value = sys.argv[1]
path = ".env"
text = open(path, encoding="utf-8").read()
text = re.sub(r"^CORS_ORIGINS=.*$", lambda m: "CORS_ORIGINS=" + value, text, count=1, flags=re.M)
open(path, "w", encoding="utf-8").write(text)
PY
    else
      printf 'CORS_ORIGINS=%s\n' "$json" >> .env
    fi
    ok "CORS_ORIGINS 已按本机 IP 更新：$json"
  fi
}

# ---------------------------------------------------------------- 端口
port_in_use() {
  local port="$1"
  # Our own containers holding the port is not a conflict — they are about to be
  # replaced. Only a *foreign* listener blocks the deploy.
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$APP_CONTAINER"; then
    return 1
  fi
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$WEB_CONTAINER"; then
    return 1
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -tlnH "sport = :$port" 2>/dev/null | grep -q . && return 0 || return 1
  fi
  # Fall back to bash's own probe when ss is unavailable.
  (exec 3<>"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1 && return 0 || return 1
}

check_ports() {
  local http_port app_port
  http_port="$(grep -E '^HTTP_PORT=' .env | tail -1 | cut -d= -f2)"
  app_port="$(grep -E '^APP_PORT=' .env | tail -1 | cut -d= -f2)"
  http_port="${http_port:-8080}"
  app_port="${app_port:-8000}"

  if port_in_use "$http_port"; then
    warn "端口 $http_port 已被其它程序占用。请在 .env 里改 HTTP_PORT 后重跑；"
    warn "或先停掉占用进程：ss -tlnp | grep :$http_port"
    die "端口冲突"
  fi
  ok "端口 $http_port / $app_port 可用"
}

# ---------------------------------------------------------------- 备份
do_backup() {
  mkdir -p "$BACKUP_DIR"
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S)"

  cp .env "$BACKUP_DIR/uh-env-$stamp.bak"
  chmod 600 "$BACKUP_DIR/uh-env-$stamp.bak"
  ok "已备份 .env → $BACKUP_DIR/uh-env-$stamp.bak"

  # 数据库没起就不备份（首次部署）
  if docker inspect "$DB_CONTAINER" >/dev/null 2>&1 \
     && [[ "$(docker inspect "$DB_CONTAINER" --format '{{.State.Running}}')" == "true" ]]; then
    local out="$BACKUP_DIR/uh-db-$stamp.sql.gz"
    if docker exec "$DB_CONTAINER" pg_dumpall -U easylearning 2>/dev/null | gzip > "$out"; then
      local size
      size="$(stat -c%s "$out" 2>/dev/null || echo 0)"
      if [[ "$size" -lt 100 ]]; then
        warn "数据库备份文件偏小（${size} 字节），请人工确认：$out"
      else
        ok "已备份数据库 → $out（$(numfmt --to=iec "$size" 2>/dev/null || echo "${size}B")）"
      fi
    else
      warn "数据库备份失败（容器可能在启动中），继续部署"
      rm -f "$out"
    fi
  else
    info "数据库容器未运行，跳过数据备份（首次部署属正常）"
  fi
}

# ---------------------------------------------------------------- 主流程
cmd_status() {
  need_docker
  local kind image digests
  kind="$(detect_existing)"
  image="$(docker inspect "$APP_CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || echo '(无)')"
  digests="$(docker inspect "$APP_CONTAINER" --format '{{json .RepoDigests}}' 2>/dev/null || echo '[]')"

  echo "部署类型： $kind"
  case "$kind" in
    none)     echo "  → 尚未部署" ;;
    upstream) echo "  → 从 GHCR 拉取的上游镜像" ;;
    fork)     echo "  → 本地构建（本 fork）" ;;
  esac
  echo
  echo "  app 镜像： $image"
  if [[ "$digests" == "[]" || -z "$digests" ]]; then
    echo "  来源判定： 无 RepoDigests → 本地 docker build 产物"
  else
    echo "  来源判定： 有 RepoDigests → 来自 registry（docker pull）"
    echo "             $digests"
  fi
  echo
  docker ps -a --filter "name=shuake-easy-learning" \
    --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' || true
}

cmd_deploy() {
  need_docker
  echo "══ 一键部署 / 更新（保留数据） ══"
  echo

  local kind image
  kind="$(detect_existing)"
  image="$(docker inspect "$APP_CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || true)"

  case "$kind" in
    upstream) report_legacy "$image" ;;
    fork)     info "检测到本 fork 的既有部署，将原地更新（数据保留）" ;;
    none)     info "未检测到既有部署，执行首次部署" ;;
  esac

  ensure_env
  check_ports
  do_backup

  echo
  info "停止旧容器（不使用 -v，数据卷保留）"
  dc down --remove-orphans 2>/dev/null || true

  echo
  info "构建并启动（首次构建约 3-8 分钟）"
  dc up -d --build

  echo
  info "等待健康检查…"
  local waited=0
  while (( waited < 300 )); do
    if curl -fsS "http://127.0.0.1:$(grep -E '^HTTP_PORT=' .env | tail -1 | cut -d= -f2 || echo 8080)/health" >/dev/null 2>&1; then
      break
    fi
    sleep 5
    waited=$((waited + 5))
    printf '.'
  done
  echo

  local http_port
  http_port="$(grep -E '^HTTP_PORT=' .env | tail -1 | cut -d= -f2)"
  http_port="${http_port:-8080}"

  local health
  health="$(curl -fsS "http://127.0.0.1:$http_port/health" 2>/dev/null || echo '(无响应)')"

  echo
  echo "══ 结果 ══"
  docker ps --filter "name=shuake-easy-learning" \
    --format 'table {{.Names}}\t{{.Status}}' || true
  echo
  echo "health: $health"

  if echo "$health" | grep -q '"schema":"ok"'; then
    ok "部署成功"
    echo
    echo "  访问地址： http://<服务器IP>:$http_port"
    local admins
    admins="$(grep -E '^ADMIN_EMAILS=' .env | tail -1 | cut -d= -f2)"
    echo "  管理员：   ${admins:-（未设置，首个注册用户为管理员）}"
  else
    warn "health 未返回 schema:ok，请查看日志："
    echo "    docker compose -p $PROJECT -f $COMPOSE_FILE logs --tail=120 app"
    exit 1
  fi
}

cmd_backup_only() {
  need_docker
  do_backup
}

main() {
  case "${1:-deploy}" in
    --status|status)       cmd_status ;;
    --backup-only|backup)  cmd_backup_only ;;
    -h|--help|help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      ;;
    *) cmd_deploy ;;
  esac
}

main "$@"
