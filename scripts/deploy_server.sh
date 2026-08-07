#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
DEPLOY_HOST="${DEPLOY_HOST:-root@47.94.3.250}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/a-share-analysis-assistant}"
DEPLOY_DOMAIN="${DEPLOY_DOMAIN:-astock.liufengliu.com}"
DEPLOY_ENABLE_HTTPS="${DEPLOY_ENABLE_HTTPS:-auto}"
REMOTE_NGINX_SITE="/etc/nginx/sites-available/$DEPLOY_DOMAIN"
REMOTE_NGINX_ENABLED="/etc/nginx/sites-enabled/$DEPLOY_DOMAIN"

if ! command -v rsync >/dev/null 2>&1; then
  echo "未找到 rsync，无法同步代码到服务器" >&2
  exit 1
fi

ssh "$DEPLOY_HOST" "mkdir -p '$DEPLOY_DIR/data'"

rsync -az --delete \
  --exclude '.git' \
  --exclude 'backend/.venv' \
  --exclude 'frontend/node_modules' \
  --exclude 'frontend/dist' \
  --exclude 'data' \
  --exclude 'data-fixture' \
  --exclude 'playwright-report' \
  --exclude 'test-results' \
  "$PROJECT_DIR/" "$DEPLOY_HOST:$DEPLOY_DIR/"

ssh "$DEPLOY_HOST" "\
  cd '$DEPLOY_DIR' && \
  docker compose -f docker-compose.prod.yml up -d --build || \
  FRONTEND_BASE_IMAGE=a-share-analysis-assistant-frontend:latest docker compose -f docker-compose.prod.yml up -d --build"

ssh "$DEPLOY_HOST" "\
  cp '$DEPLOY_DIR/deploy/astock.nginx.conf' '$REMOTE_NGINX_SITE' && \
  ln -sfn '$REMOTE_NGINX_SITE' '$REMOTE_NGINX_ENABLED' && \
  nginx -t && \
  systemctl reload nginx"

ssh "$DEPLOY_HOST" "\
  if [ '$DEPLOY_ENABLE_HTTPS' = 'true' ] || { [ '$DEPLOY_ENABLE_HTTPS' = 'auto' ] && [ -f '/etc/letsencrypt/live/$DEPLOY_DOMAIN/fullchain.pem' ]; }; then \
    certbot --nginx -d '$DEPLOY_DOMAIN' --redirect --non-interactive; \
  fi"

echo "部署完成：http://$DEPLOY_DOMAIN"
