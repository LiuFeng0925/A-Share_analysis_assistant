#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"

if [ ! -x "$PROJECT_DIR/backend/.venv/bin/pytest" ]; then
  echo "测试依赖尚未安装，请先运行 ./scripts/bootstrap.sh" >&2
  exit 1
fi
if ! command -v pnpm >/dev/null 2>&1; then
  echo "未找到 pnpm，请先运行 ./scripts/bootstrap.sh" >&2
  exit 1
fi

"$PROJECT_DIR/backend/.venv/bin/pytest" "$PROJECT_DIR/backend/tests" -q
"$PROJECT_DIR/backend/.venv/bin/ruff" check \
  "$PROJECT_DIR/backend/src" "$PROJECT_DIR/backend/tests" "$PROJECT_DIR/scripts/smoke_live.py"
pnpm --dir "$PROJECT_DIR/frontend" test
pnpm --dir "$PROJECT_DIR/frontend" build
echo "后端、前端测试与生产构建全部通过"
