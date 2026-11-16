#!/bin/bash
# Установка mailbot в Docker одной командой.
# Использование: bash docker-install.sh ТОКЕН_БОТА [ТВОЙ_TELEGRAM_ID]
set -e
TOKEN="$1"
ID="${2:-}"
[ -z "$TOKEN" ] && { echo "Укажи токен: bash docker-install.sh ТОКЕН [ID]"; exit 1; }

BRANCH="ccr-5d79bc22-6d67we"
SRC_DIR="/opt/mailbot-src"
DATA_DIR="/opt/mailbot-data"

command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh
command -v git >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq git)

rm -rf "$SRC_DIR"
git clone --depth 1 --branch "$BRANCH" https://github.com/veritesClaude/hunay "$SRC_DIR"

mkdir -p "$DATA_DIR"
# Ключ шифрования паролей хранится в $DATA_DIR и переиспользуется при обновлении (иначе старые пароли не расшифруются).
KEY_FILE="$DATA_DIR/secret_key"
if [ ! -s "$KEY_FILE" ] && docker inspect mailbot >/dev/null 2>&1; then
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' mailbot | sed -n 's/^SECRET_KEY=//p' > "$KEY_FILE" || true
fi
[ -s "$KEY_FILE" ] || { openssl rand -base64 32 | tr '+/' '-_' > "$KEY_FILE"; chmod 600 "$KEY_FILE"; }
SECRET_KEY=$(cat "$KEY_FILE")
# Необязательно: Client ID приложения Microsoft для входа в Outlook (MS_CLIENT_ID=... перед bash).
MS_FILE="$DATA_DIR/ms_client_id"
if [ -n "${MS_CLIENT_ID:-}" ]; then echo "$MS_CLIENT_ID" > "$MS_FILE"; fi
MS_CLIENT_ID=$( [ -s "$MS_FILE" ] && cat "$MS_FILE" || true )

# Секрет для вебхука SMS (в адресе), хранится в $DATA_DIR и не меняется при обновлениях.
WH_FILE="$DATA_DIR/webhook_key"
[ -s "$WH_FILE" ] || { openssl rand -hex 16 > "$WH_FILE"; chmod 600 "$WH_FILE"; }
WEBHOOK_KEY=$(cat "$WH_FILE")
WEBHOOK_PORT="${WEBHOOK_PORT:-8080}"
PUBLIC_HOST="${WEBHOOK_HOST:-$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo ВАШ_IP_ИЛИ_ДОМЕН)}"

docker build -t mailbot "$SRC_DIR/mailbot"
docker rm -f mailbot >/dev/null 2>&1 || true
docker run -d --name mailbot --restart unless-stopped \
  -e BOT_TOKEN="$TOKEN" \
  -e SECRET_KEY="$SECRET_KEY" \
  -e ALLOWED_IDS="$ID" \
  -e MS_CLIENT_ID="$MS_CLIENT_ID" \
  -e WEBHOOK_KEY="$WEBHOOK_KEY" -e WEBHOOK_PORT="$WEBHOOK_PORT" -e WEBHOOK_HOST="$PUBLIC_HOST" \
  -p "$WEBHOOK_PORT:$WEBHOOK_PORT" \
  -v "$DATA_DIR:/data" \
  mailbot

cat > /usr/local/bin/mailbot-admin <<'EOS'
#!/bin/bash
exec docker exec mailbot python admin.py "$@"
EOS
chmod +x /usr/local/bin/mailbot-admin

sleep 2
docker ps --filter name=mailbot --format "{{.Status}}"
echo "ГОТОВО: бот запущен в Docker. Логи: docker logs -f mailbot"
echo
echo "Адрес для вебхука SMS (вставь в сервис номеров; порт $WEBHOOK_PORT должен быть открыт):"
echo "http://$PUBLIC_HOST:$WEBHOOK_PORT/smswebhook?key=$WEBHOOK_KEY&from=%FROM%&to=%TO%&msg=%MESSAGE%"
echo "Показать снова: mailbot-admin webhook"
