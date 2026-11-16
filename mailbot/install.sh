#!/bin/bash
# Использование: bash install.sh ТОКЕН_БОТА [ТВОЙ_TELEGRAM_ID]
set -e
[ -z "$1" ] && { echo "Укажи токен: bash install.sh ТОКЕН [ID]"; exit 1; }
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
apt-get install -y python3 python3-venv >/dev/null 2>&1 || true
python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt
KEY=$(./venv/bin/python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
cat > .env <<ENV
BOT_TOKEN=$1
SECRET_KEY=$KEY
ALLOWED_IDS=${2:-}
CHECK_INTERVAL=60
ENV
chmod 600 .env
cat > /etc/systemd/system/mailbot.service <<UNIT
[Unit]
Description=Mail to Telegram bot
After=network-online.target
[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python bot.py
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now mailbot
sleep 2
systemctl is-active mailbot && echo "ГОТОВО: бот запущен. Напиши ему /start"
