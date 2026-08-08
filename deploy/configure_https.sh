#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DOMAIN="${1:?缺少部署域名}"
DEPLOY_ENABLE_HTTPS="${2:-auto}"
CERTBOT_LIVE_DIR="${CERTBOT_LIVE_DIR:-/etc/letsencrypt/live}"

case "$DEPLOY_ENABLE_HTTPS" in
  true)
    certbot --nginx -d "$DEPLOY_DOMAIN" --redirect --non-interactive
    ;;
  auto)
    if [ -f "$CERTBOT_LIVE_DIR/$DEPLOY_DOMAIN/fullchain.pem" ]; then
      certbot install \
        --nginx \
        --cert-name "$DEPLOY_DOMAIN" \
        --redirect \
        --non-interactive
    fi
    ;;
  false)
    ;;
  *)
    echo "不支持的 DEPLOY_ENABLE_HTTPS：$DEPLOY_ENABLE_HTTPS" >&2
    exit 2
    ;;
esac
