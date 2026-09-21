#!/usr/bin/env bash
#
# 意林杯后端生产部署脚本 —— 在服务器上执行。
#
# 正常由 .github/workflows/deploy.yml 经 SSH 送入 /tmp 后运行，所以每次跑的
# 都是本次要发布的这一份；也可以登录服务器后手工执行：
#
#     bash /www/wwwroot/yilinbei/deploy/deploy.sh
#
# 本脚本不触碰 .env：生产配置只存在于服务器本地，且已被 .gitignore 排除，
# 因此 git reset --hard 不会覆盖它。
#
# 任何一步失败都以非 0 退出，让 GitHub Actions 明确失败，不会出现"实际失败但
# 显示成功"。
#
set -euo pipefail

# --- 生产环境事实 -----------------------------------------------------------------
# 以下全部是 2026-09-22 在服务器上实测确认的，没有任何推测成分：
#
#   项目目录 : /www/wwwroot/yilinbei          （git 工作区，跟踪 master）
#   虚拟环境 : /www/wwwroot/yilinbei/venv     （Python 3.10.11）
#   启动命令 : venv/bin/gunicorn --bind 127.0.0.1:8000 django_config.wsgi:application
#   监听     : 127.0.0.1:8000（仅本机，不对外）
#   Nginx    : server_name 47.108.29.34, listen 80 -> proxy_pass http://127.0.0.1:8000
#   服务管理 : 【无】。systemd 里没有对应单元，supervisor 未安装，宝塔的 Python
#              项目管理器也未启用（插件目录为空）。gunicorn 是当初从命令行手工
#              启动后被 daemon 化的裸进程（PPID=1）。
#
# 因此重启方式按现状原样复刻 —— 同样的解释器、同样的参数、同样的工作目录 ——
# 不新建 systemd 单元或 supervisor 配置，避免出现第二套服务。
# ---------------------------------------------------------------------------------

APP_DIR="/www/wwwroot/yilinbei"
VENV_DIR="$APP_DIR/venv"
BRANCH="master"

# 与线上正在运行的进程逐字一致。改动它等于改变生产启动方式。
GUNICORN_ARGS=(--bind 127.0.0.1:8000 django_config.wsgi:application)
# 用于识别旧进程的匹配串，必须与上面的参数对应。
GUNICORN_PATTERN="gunicorn --bind 127.0.0.1:8000 django_config.wsgi:application"

LOCAL_HEALTH_URL="http://127.0.0.1:8000/api/health"

# 日志必须落在仓库目录之外：仓库内一旦出现未跟踪文件，下一次部署的
# "工作树必须干净" 检查就会失败。
LOG_FILE="/www/wwwlogs/yilinbei-gunicorn.log"
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || LOG_FILE="/var/log/yilinbei-gunicorn.log"

BEFORE_SHA=""

log()  { printf '\n[%s] %s\n' "$1" "$2"; }
fail() { printf '\n部署失败：%s\n' "$1" >&2; exit 1; }

on_exit() {
  local code=$?
  if [ "$code" -ne 0 ]; then
    printf '\n部署失败，退出码 %s。\n' "$code" >&2
    printf '服务器当前代码：%s\n' "$(cd "$APP_DIR" 2>/dev/null && git rev-parse --short HEAD 2>/dev/null || echo '?')" >&2
    if [ -n "$BEFORE_SHA" ]; then
      printf '部署前版本为 %s。如需回滚代码，手工执行：\n' "${BEFORE_SHA:0:7}" >&2
      printf '  cd %s && git reset --hard %s\n' "$APP_DIR" "$BEFORE_SHA" >&2
      printf '（回滚代码不会撤销已执行的数据库迁移，需要另行评估。）\n' >&2
    fi
  fi
  # 显式以原退出码结束：EXIT trap 里最后一条命令的状态会影响脚本退出码，
  # 不能让它把失败"洗成"成功。
  exit "$code"
}
trap on_exit EXIT

command -v pgrep >/dev/null 2>&1 || fail "服务器缺少 pgrep（procps-ng）"
command -v curl  >/dev/null 2>&1 || fail "服务器缺少 curl"

cd "$APP_DIR" || fail "项目目录不存在：$APP_DIR"

# ---------------------------------------------------------------- 1/9 工作树
log "1/9" "Checking git worktree..."
if [ -n "$(git status --porcelain)" ]; then
  echo "服务器工作树存在未提交的改动，拒绝部署，以免覆盖它们：" >&2
  git status --short >&2
  echo "请人工确认这些改动后再重新触发部署。" >&2
  exit 1
fi
echo "工作树干净。"

# ---------------------------------------------------------------- 2/9 分支
log "2/9" "Verifying branch..."
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$CURRENT_BRANCH" = "$BRANCH" ] || fail "当前分支是 $CURRENT_BRANCH，期望 $BRANCH"
echo "当前分支：$CURRENT_BRANCH"

# ---------------------------------------------------------------- 3/9 代码
log "3/9" "Updating source code..."
BEFORE_SHA="$(git rev-parse HEAD)"
git fetch --prune origin "$BRANCH"
git reset --hard "origin/$BRANCH"
AFTER_SHA="$(git rev-parse HEAD)"
echo "代码：${BEFORE_SHA:0:7} -> ${AFTER_SHA:0:7}"

# ---------------------------------------------------------------- 4/9 依赖
log "4/9" "Installing dependencies..."
"$VENV_DIR/bin/pip" install --disable-pip-version-check --quiet -r requirements.txt
echo "依赖安装完成。"

# ---------------------------------------------------------------- 5/9 检查
log "5/9" "Running Django checks..."
# --deploy 会输出 HSTS / SSL 重定向之类的 warning，但 warning 不影响退出码，
# 只有真正的配置错误才会让这里失败。
"$VENV_DIR/bin/python" manage.py check --deploy

# ---------------------------------------------------------------- 6/9 迁移
log "6/9" "Running migrations..."
"$VENV_DIR/bin/python" manage.py migrate --noinput

# ---------------------------------------------------------------- 7/9 静态
log "7/9" "Collecting static files..."
# whitenoise 挂在 MIDDLEWARE 里且 STATIC_ROOT 已配置，说明静态文件由本进程直接
# 提供，因此需要 collectstatic。产物落在 staticfiles/，已被 .gitignore 排除，
# 不会影响下一次部署的工作树检查。
"$VENV_DIR/bin/python" manage.py collectstatic --noinput

# ---------------------------------------------------------------- 8/9 重启
log "8/9" "Restarting backend..."

stop_backend() {
  local pids master="" pid ppid
  pids="$(pgrep -f "$GUNICORN_PATTERN" || true)"
  if [ -z "$pids" ]; then
    echo "未发现运行中的 gunicorn（首次部署，或此前已停止）。"
    return 0
  fi

  # TERM 让 gunicorn 优雅退出：先停止接受新连接，处理完在途请求再关闭 worker。
  # 优先只通知 master（PPID=1 的那个），由它去收 worker —— 若逐个 TERM worker，
  # master 可能在等待期间又补起一个，白白等到超时。
  for pid in $pids; do
    ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
    if [ "$ppid" = "1" ]; then master="$pid"; break; fi
  done

  if [ -n "$master" ]; then
    echo "向 master($master) 发送 TERM；全部 PID：$(echo "$pids" | tr '\n' ' ' | sed 's/ $//')"
    kill -TERM "$master" || true
  else
    echo "未识别出 master，向全部进程发送 TERM：$(echo "$pids" | tr '\n' ' ' | sed 's/ $//')"
    kill -TERM $pids || true
  fi

  local i
  for i in $(seq 1 20); do
    if ! pgrep -f "$GUNICORN_PATTERN" >/dev/null 2>&1; then
      echo "旧进程已退出。"
      return 0
    fi
    sleep 1
  done
  echo "旧进程 20 秒内未退出，强制结束。" >&2
  pkill -KILL -f "$GUNICORN_PATTERN" || true
  sleep 1
}

start_backend() {
  cd "$APP_DIR"
  # 与线上原命令逐字一致。nohup 让进程无视 SIGHUP，从而在 SSH 会话结束后继续
  # 存活；输出重定向到文件，既避免占用 SSH 通道，也留下排障所需的日志。
  nohup "$VENV_DIR/bin/gunicorn" "${GUNICORN_ARGS[@]}" >>"$LOG_FILE" 2>&1 &
  echo "已启动新进程 PID=$!，日志：$LOG_FILE"
}

stop_backend
start_backend

# ---------------------------------------------------------------- 9/9 健康检查
log "9/9" "Running health check..."
# 这里只探本机 8000，验证"进程起来了 + 能连上数据库"。公网那一跳由 GitHub
# Actions 侧再走一遍，两段都过才算部署成功。
HTTP_CODE="000"
for _ in $(seq 1 20); do
  HTTP_CODE="$(curl -s -o /tmp/ylb-health.json -w '%{http_code}' --max-time 5 "$LOCAL_HEALTH_URL" || echo 000)"
  [ "$HTTP_CODE" = "200" ] && break
  sleep 1
done

echo "GET $LOCAL_HEALTH_URL -> HTTP $HTTP_CODE"
cat /tmp/ylb-health.json 2>/dev/null || true
echo

if [ "$HTTP_CODE" != "200" ]; then
  echo "健康检查未通过（期望 200）。/api/health 会真实探测数据库，返回 503 表示数据库不可达。" >&2
  echo "----- gunicorn 日志尾部 -----" >&2
  tail -n 40 "$LOG_FILE" >&2 2>/dev/null || echo "(读不到 $LOG_FILE)" >&2
  exit 1
fi

echo
echo "Deployment successful."
