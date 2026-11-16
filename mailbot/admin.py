"""Админ-команды, запускаются только в консоли сервера (не через бота):
  mailbot-admin list            — все участники, включая скрытых
  mailbot-admin hide ID         — скрыть участника из списка «👥 Участники» (доступ и уведомления остаются)
  mailbot-admin unhide ID       — снова показывать
  mailbot-admin webhook         — адрес для пересылки SMS (вебхук)
"""
import os, sqlite3, sys

db = sqlite3.connect(os.getenv("DB_PATH", "mailbot.db"), timeout=10)
db.execute("""CREATE TABLE IF NOT EXISTS users(
 user_id INTEGER PRIMARY KEY, name TEXT DEFAULT '', username TEXT DEFAULT '',
 joined_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
try:
    db.execute("ALTER TABLE users ADD COLUMN hidden INTEGER DEFAULT 0")
except sqlite3.OperationalError:
    pass

def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "list":
        for uid, name, un, hid in db.execute(
                "SELECT user_id, name, username, COALESCE(hidden,0) FROM users ORDER BY joined_at, user_id"):
            print(f"{uid}\t{name or '-'}\t{('@' + un) if un else '-'}\t{'СКРЫТ' if hid else ''}")
        return 0
    if cmd == "webhook":
        key, port = os.getenv("WEBHOOK_KEY", ""), os.getenv("WEBHOOK_PORT", "8080")
        if not key:
            print("Вебхук выключен (нет WEBHOOK_KEY). Перезапусти установку."); return 1
        host = os.getenv("WEBHOOK_HOST", "ВАШ_IP_ИЛИ_ДОМЕН")
        print(f"http://{host}:{port}/smswebhook?key={key}&from=%FROM%&to=%TO%&msg=%MESSAGE%")
        return 0
    if cmd in ("hide", "unhide") and len(argv) == 2 and argv[1].lstrip("-").isdigit():
        uid, flag = int(argv[1]), 1 if cmd == "hide" else 0
        exists = db.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone()
        if exists:
            db.execute("UPDATE users SET hidden=? WHERE user_id=?", (flag, uid))
        elif flag:
            db.execute("INSERT INTO users(user_id, hidden) VALUES(?, 1)", (uid,))
            print("Этого ID ещё не было среди участников — добавлен сразу скрытым "
                  "(уведомления пойдут, когда он напишет боту /start).")
        else:
            print("Такого участника нет."); return 1
        db.commit()
        print(f"Готово: {uid} {'скрыт' if flag else 'снова виден'}.")
        return 0
    print(__doc__); return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
