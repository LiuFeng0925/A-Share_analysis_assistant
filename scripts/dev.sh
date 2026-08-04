#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
BACKEND_PORT="${A_SHARE_BACKEND_PORT:-8000}"
FRONTEND_PORT="${A_SHARE_FRONTEND_PORT:-5173}"
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  status=$?
  trap - EXIT INT TERM
  for pid in "$BACKEND_PID" "$FRONTEND_PID"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$BACKEND_PID" "$FRONTEND_PID"; do
    if [ -n "$pid" ]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ ! -x "$PROJECT_DIR/backend/.venv/bin/uvicorn" ]; then
  echo "后端依赖尚未安装，请先运行 ./scripts/bootstrap.sh" >&2
  exit 1
fi
if ! command -v pnpm >/dev/null 2>&1; then
  echo "未找到 pnpm，请先运行 ./scripts/bootstrap.sh" >&2
  exit 1
fi

A_SHARE_DATA_DIR="${A_SHARE_DATA_DIR:-$PROJECT_DIR/data}" \
  "$PROJECT_DIR/backend/.venv/bin/uvicorn" a_share_radar.main:app \
  --app-dir "$PROJECT_DIR/backend/src" --reload \
  --reload-dir "$PROJECT_DIR/backend/src" \
  --host 127.0.0.1 --port "$BACKEND_PORT" &
BACKEND_PID=$!

VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://127.0.0.1:$BACKEND_PORT}" \
  pnpm --dir "$PROJECT_DIR/frontend" dev --host 127.0.0.1 --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

echo "A 股雷达正在启动：http://127.0.0.1:$FRONTEND_PORT"
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done

if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
  wait "$BACKEND_PID" || status=$?
  echo "后端服务异常退出，请检查端口 $BACKEND_PORT 是否占用或查看上方日志" >&2
else
  wait "$FRONTEND_PID" || status=$?
  echo "前端服务异常退出，请检查端口 $FRONTEND_PORT 是否占用或查看上方日志" >&2
fi
exit "${status:-1}"
