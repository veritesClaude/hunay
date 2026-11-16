#!/bin/bash
# Установка mailbot в Docker одной командой.
# Использование: bash docker-install.sh ТОКЕН_БОТА [ТВОЙ_TELEGRAM_ID]
set -e
TOKEN="$1"
ID="${2:-}"
[ -z "$TOKEN" ] && { echo "Укажи токен: bash docker-install.sh ТОКЕН [ID]"; exit 1; }

BRANCH="ccr-dd0fe0f8-todiym"
SRC_DIR="/opt/mailbot-src"
DATA_DIR="/opt/mailbot-data"

command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh
command -v git >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq git)

rm -rf "$SRC_DIR"
git clone --depth 1 --branch "$BRANCH" https://github.com/veritesClaude/hunay "$SRC_DIR"

SECRET_KEY=$(openssl rand -base64 32 | tr '+/' '-_')
mkdir -p "$DATA_DIR"

docker build -t mailbot "$SRC_DIR/mailbot"
docker rm -f mailbot >/dev/null 2>&1 || true
docker run -d --name mailbot --restart unless-stopped \
  -e BOT_TOKEN="$TOKEN" \
  -e SECRET_KEY="$SECRET_KEY" \
  -e ALLOWED_IDS="$ID" \
  -v "$DATA_DIR:/data" \
  mailbot

sleep 2
docker ps --filter name=mailbot --format "{{.Status}}"
echo "ГОТОВО: бот запущен в Docker. Логи: docker logs -f mailbot"
