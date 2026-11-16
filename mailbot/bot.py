import asyncio, imaplib, email, os, re, sqlite3, html, logging
from email.header import decode_header, make_header
from email.utils import parseaddr
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]
FERNET = Fernet(os.environ["SECRET_KEY"].encode())
ALLOWED = {int(x) for x in os.getenv("ALLOWED_IDS", "").replace(" ", "").split(",") if x}
INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
DB = os.getenv("DB_PATH", "mailbot.db")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mailbot")

HOSTS = {
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "yandex.ru": "imap.yandex.ru", "ya.ru": "imap.yandex.ru", "yandex.com": "imap.yandex.com",
    "mail.ru": "imap.mail.ru", "inbox.ru": "imap.mail.ru", "list.ru": "imap.mail.ru", "bk.ru": "imap.mail.ru",
    "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
    "icloud.com": "imap.mail.me.com", "me.com": "imap.mail.me.com",
    "rambler.ru": "imap.rambler.ru", "yahoo.com": "imap.mail.yahoo.com",
}
HELP_PASS = {
    "gmail.com": "Gmail: включи двухэтапную аутентификацию и создай пароль приложения: myaccount.google.com/apppasswords",
    "yandex.ru": "Яндекс: id.yandex.ru → Безопасность → Пароли приложений (тип «Почта»), и включи IMAP в настройках почты.",
    "mail.ru": "Mail.ru: Настройки → Пароли для внешних приложений → создай пароль для почтового клиента.",
}

db = sqlite3.connect(DB, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS accounts(
 id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, email TEXT, pw TEXT,
 host TEXT, last_uid INTEGER DEFAULT 0, UNIQUE(chat_id,email))""")
db.execute("""CREATE TABLE IF NOT EXISTS category_prefs(
 chat_id INTEGER, category TEXT, notify INTEGER DEFAULT 1, body INTEGER DEFAULT 1,
 PRIMARY KEY(chat_id,category))""")
db.commit()
dblock = asyncio.Lock()

# Категории писем: правило срабатывает по теме/тексту, порядок важен (первое совпадение побеждает).
CATEGORIES = {
    "booking_request": "📩 Заявка на бронирование (Monteurzimmer)",
    "listing_approved": "✅ Объявление одобрено (Monteurzimmer)",
    "listing_rejected": "❌ Объявление отклонено (Monteurzimmer)",
    "sms_code": "📱 SMS-коды (WhatsApp и т.п.)",
    "other": "✉️ Остальные письма",
}
CATEGORY_ORDER = ["booking_request", "listing_approved", "listing_rejected", "sms_code", "other"]

def classify(subject, body):
    s = (subject or "").lower()
    b = (body or "").lower()
    if "monteurzimmer" in s:
        if "buchungsanfrage" in s:
            return "booking_request"
        if "nicht veröffentlicht" in s or "nicht freigegeben" in s:
            return "listing_rejected"
        if "freigegeben" in s:
            return "listing_approved"
    if "business code" in b or "received sms from" in b:
        return "sms_code"
    return "other"

def get_pref(chat_id, cat):
    row = db.execute("SELECT notify, body FROM category_prefs WHERE chat_id=? AND category=?",
                      (chat_id, cat)).fetchone()
    return row if row else (1, 1)

def toggle_pref(chat_id, cat, field):
    db.execute("INSERT OR IGNORE INTO category_prefs(chat_id,category,notify,body) VALUES(?,?,1,1)",
               (chat_id, cat))
    db.execute(f"UPDATE category_prefs SET {field}=1-{field} WHERE chat_id=? AND category=?", (chat_id, cat))
    db.commit()

def categories_kb(chat_id):
    rows = []
    for cat in CATEGORY_ORDER:
        notify, body = get_pref(chat_id, cat)
        rows.append([InlineKeyboardButton(text=CATEGORIES[cat], callback_data="noop")])
        rows.append([
            InlineKeyboardButton(text=("🔔 Уведомлять: Вкл" if notify else "🔕 Уведомлять: Выкл"),
                                  callback_data=f"catnotify:{cat}"),
            InlineKeyboardButton(text=("📄 Текст письма: Вкл" if body else "📄 Текст письма: Выкл"),
                                  callback_data=f"catbody:{cat}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

class Add(StatesGroup):
    email = State(); host = State(); pw = State()

dp = Dispatcher(storage=MemoryStorage())
bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

def allowed(m: Message) -> bool:
    return not ALLOWED or m.from_user.id in ALLOWED

def dec(v):
    try: return str(make_header(decode_header(v or "")))
    except Exception: return v or ""

def imap_connect(host, user, pw):
    c = imaplib.IMAP4_SSL(host, 993, timeout=30)
    c.login(user, pw)
    c.select("INBOX", readonly=True)
    return c

def max_uid(c):
    typ, d = c.uid("search", None, "ALL")
    ids = d[0].split() if d and d[0] else []
    return int(ids[-1]) if ids else 0

def body_text(msg):
    plain = html_part = None
    for p in msg.walk():
        if p.get_content_maintype() == "multipart" or p.get_filename(): continue
        ct = p.get_content_type()
        try: t = p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace")
        except Exception: continue
        if ct == "text/plain" and plain is None: plain = t
        elif ct == "text/html" and html_part is None: html_part = t
    if plain: return plain.strip()
    if html_part:
        t = re.sub(r"(?is)<(script|style).*?</\1>", "", html_part)
        t = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", t)
        return html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
    return ""

def fetch_new(host, user, pw, last_uid):
    c = imap_connect(host, user, pw)
    try:
        typ, d = c.uid("search", None, f"UID {last_uid + 1}:*")
        uids = sorted(int(x) for x in (d[0].split() if d and d[0] else []) if int(x) > last_uid)
        out = []
        for u in uids[:20]:
            typ, data = c.uid("fetch", str(u), "(BODY.PEEK[])")
            if not data or not data[0]: continue
            msg = email.message_from_bytes(data[0][1])
            name, addr = parseaddr(dec(msg.get("From")))
            files = [dec(p.get_filename()) for p in msg.walk() if p.get_filename()]
            out.append((u, name or addr, addr, dec(msg.get("Subject")) or "(без темы)",
                        dec(msg.get("Date")), re.sub(r"\n{3,}", "\n\n", body_text(msg))[:1500], files))
        return out, (uids[-1] if len(uids) > 20 and False else (uids[min(len(uids), 20) - 1] if uids else last_uid))
    finally:
        try: c.logout()
        except Exception: pass

async def poll():
    while True:
        await asyncio.sleep(INTERVAL)
        rows = db.execute("SELECT id,chat_id,email,pw,host,last_uid FROM accounts").fetchall()
        for id_, chat, em, pw, host, last in rows:
            try:
                msgs, newlast = await asyncio.to_thread(fetch_new, host, em, FERNET.decrypt(pw.encode()).decode(), last)
                for u, name, addr, subj, date, body, files in msgs:
                    cat = classify(subj, body)
                    notify, show_body = get_pref(chat, cat)
                    if not notify:
                        continue
                    text = (f"📬 <b>Новое письмо</b> → {html.escape(em)}\n"
                            f"<b>От:</b> {html.escape(name)} &lt;{html.escape(addr)}&gt;\n"
                            f"<b>Тема:</b> {html.escape(subj)}\n<b>Дата:</b> {html.escape(date)}\n")
                    if cat != "other": text += f"<b>Тип:</b> {html.escape(CATEGORIES[cat])}\n"
                    if files: text += "📎 " + html.escape(", ".join(files)) + "\n"
                    if body and show_body: text += "\n" + html.escape(body)
                    await bot.send_message(chat, text[:4000])
                if newlast != last:
                    db.execute("UPDATE accounts SET last_uid=? WHERE id=?", (newlast, id_)); db.commit()
            except Exception as e:
                log.warning("poll %s: %s", em, e)

@dp.message(Command("start", "help"))
async def start(m: Message):
    if not allowed(m): return
    await m.answer("Я пересылаю новые письма из твоей почты сюда.\n\n"
                   "/add — привязать почту\n/list — список привязанных ящиков\n"
                   "/remove — отвязать ящик\n/categories — включить/выключить уведомления по типам писем\n"
                   "/cancel — отменить текущее действие")

@dp.message(Command("categories"))
async def categories_cmd(m: Message):
    if not allowed(m): return
    await m.answer("Настрой уведомления по типам писем (жми на кнопки, чтобы переключить):",
                   reply_markup=categories_kb(m.chat.id))

@dp.callback_query(F.data == "noop")
async def cat_noop(cq: CallbackQuery):
    await cq.answer()

@dp.callback_query(F.data.startswith("catnotify:") | F.data.startswith("catbody:"))
async def cat_toggle(cq: CallbackQuery):
    if ALLOWED and cq.from_user.id not in ALLOWED:
        return await cq.answer()
    action, cat = cq.data.split(":", 1)
    toggle_pref(cq.message.chat.id, cat, "notify" if action == "catnotify" else "body")
    await cq.message.edit_reply_markup(reply_markup=categories_kb(cq.message.chat.id))
    await cq.answer("Обновлено")

@dp.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear(); await m.answer("Отменено.")

@dp.message(Command("add"))
async def add(m: Message, state: FSMContext):
    if not allowed(m): return
    await state.set_state(Add.email)
    await m.answer("Напиши адрес почты, например <code>name@gmail.com</code>")

@dp.message(Add.email)
async def add_email(m: Message, state: FSMContext):
    em = (m.text or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", em):
        return await m.answer("Не похоже на адрес почты. Попробуй ещё раз или /cancel")
    dom = em.split("@")[1]
    await state.update_data(email=em)
    if dom in HOSTS:
        await state.update_data(host=HOSTS[dom]); await state.set_state(Add.pw)
        await m.answer(HELP_PASS.get(dom, "Нужен пароль приложения (не основной пароль).") +
                       "\n\nОтправь пароль приложения. Сообщение с ним я сразу удалю.")
    else:
        await state.set_state(Add.host)
        await m.answer(f"Не знаю IMAP-сервер для {dom}. Напиши его, например <code>imap.{dom}</code>")

@dp.message(Add.host)
async def add_host(m: Message, state: FSMContext):
    await state.update_data(host=(m.text or "").strip()); await state.set_state(Add.pw)
    await m.answer("Отправь пароль (лучше пароль приложения). Сообщение с ним я сразу удалю.")

@dp.message(Add.pw)
async def add_pw(m: Message, state: FSMContext):
    d = await state.get_data(); pw = (m.text or "").strip()
    try: await m.delete()
    except Exception: pass
    try:
        def check():
            c = imap_connect(d["host"], d["email"], pw)
            u = max_uid(c); c.logout(); return u
        uid = await asyncio.to_thread(check)
    except Exception as e:
        await state.clear()
        return await m.answer(f"❌ Не удалось войти: <code>{html.escape(str(e))[:300]}</code>\n"
                              "Проверь, что включён IMAP и используется пароль приложения. Повтори: /add")
    db.execute("INSERT OR REPLACE INTO accounts(chat_id,email,pw,host,last_uid) VALUES(?,?,?,?,?)",
               (m.chat.id, d["email"], FERNET.encrypt(pw.encode()).decode(), d["host"], uid))
    db.commit(); await state.clear()
    await m.answer(f"✅ <b>{html.escape(d['email'])}</b> привязана. Старые письма не придут, только новые "
                   f"(проверка раз в {INTERVAL} сек).")

@dp.message(Command("list"))
async def lst(m: Message):
    if not allowed(m): return
    rows = db.execute("SELECT email FROM accounts WHERE chat_id=?", (m.chat.id,)).fetchall()
    await m.answer("\n".join("• " + html.escape(r[0]) for r in rows) if rows else "Пока ничего не привязано. /add")

@dp.message(Command("remove"))
async def rem(m: Message):
    if not allowed(m): return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await m.answer("Напиши так: <code>/remove name@gmail.com</code>")
    db.execute("DELETE FROM accounts WHERE chat_id=? AND email=?", (m.chat.id, parts[1].strip().lower()))
    db.commit(); await m.answer("Готово, ящик отвязан.")

async def main():
    asyncio.create_task(poll())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
