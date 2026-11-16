# Mail → Telegram бот

Привязываешь почту через `/add`, новые письма приходят в Telegram.

## Установка в Docker, одной командой (на сервере, от root)

```
curl -fsSL https://raw.githubusercontent.com/veritesClaude/hunay/ccr-dd0fe0f8-todiym/mailbot/docker-install.sh | bash -s -- ТОКЕН_БОТА ТВОЙ_TELEGRAM_ID
```

Сам поставит Docker (если его нет), склонирует код, соберёт образ и запустит контейнер `mailbot` с `--restart unless-stopped`. Данные (SQLite) лежат в `/opt/mailbot-data` на хосте, переживают пересборку и перезапуск.

Токен берёшь у `@BotFather`, свой ID — у `@userinfobot`. `ALLOWED_IDS` можно оставить пустым — тогда бот отвечает всем, кто напишет.

Логи: `docker logs -f mailbot`
Остановить: `docker stop mailbot`
Обновить: удалить `/opt/mailbot-src`, перезапустить ту же команду.

## Установка без Docker (systemd)

```
unzip mailbot.zip -d mailbot && cd mailbot && bash install.sh ТОКЕН_БОТА ТВОЙ_ID
```

Логи: `journalctl -u mailbot -f`
