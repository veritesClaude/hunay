import asyncio, imaplib, email, os, re, socket, sqlite3, html, logging
from email.header import decode_header, make_header
from email.utils import parseaddr
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                            ReplyKeyboardMarkup, KeyboardButton, BotCommand)
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
    "gmx.de": "imap.gmx.net", "gmx.net": "imap.gmx.net", "gmx.at": "imap.gmx.net",
    "gmx.ch": "imap.gmx.net", "gmx.com": "imap.gmx.net",
    "web.de": "imap.web.de",
    "t-online.de": "secureimap.t-online.de",
    "freenet.de": "mx.freenet.de",
    "1und1.de": "imap.1und1.de", "online.de": "imap.1und1.de",
    "ionos.de": "imap.ionos.de",
    "posteo.de": "posteo.de", "posteo.net": "posteo.de",
    "mailbox.org": "imap.mailbox.org",
    "aol.com": "imap.aol.com", "zoho.com": "imap.zoho.com",
}
HELP_PASS = {
    "gmail.com": "Gmail: включи двухэтапную аутентификацию и создай пароль приложения: myaccount.google.com/apppasswords",
    "yandex.ru": "Яндекс: id.yandex.ru → Безопасность → Пароли приложений (тип «Почта»), и включи IMAP в настройках почты.",
    "mail.ru": "Mail.ru: Настройки → Пароли для внешних приложений → создай пароль для почтового клиента.",
}
_GMX = ("GMX: зайди в почту → Einstellungen → E-Mail → POP3 &amp; IMAP → включи «Zugriff auf GMX Postfach per "
        "POP3 und IMAP erlauben». Если включена двухфакторка — создай «Anwendungsspezifisches Passwort» "
        "(Einstellungen → Sicherheit) и используй его.")
for _d in ("gmx.de", "gmx.net", "gmx.at", "gmx.ch", "gmx.com"):
    HELP_PASS[_d] = _GMX
HELP_PASS["web.de"] = _GMX.replace("GMX", "WEB.DE").replace("GMX Postfach", "WEB.DE Postfach")

db = sqlite3.connect(DB, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS accounts(
 id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, email TEXT, pw TEXT,
 host TEXT, last_uid INTEGER DEFAULT 0, UNIQUE(chat_id,email))""")
db.execute("""CREATE TABLE IF NOT EXISTS category_prefs(
 chat_id INTEGER, category TEXT, notify INTEGER DEFAULT 1, body INTEGER DEFAULT 1,
 PRIMARY KEY(chat_id,category))""")
db.execute("""CREATE TABLE IF NOT EXISTS users(
 user_id INTEGER PRIMARY KEY, name TEXT DEFAULT '', username TEXT DEFAULT '',
 joined_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
db.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
db.execute("INSERT OR IGNORE INTO users(user_id) SELECT DISTINCT chat_id FROM accounts")
for _id in ALLOWED:
    db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (_id,))
db.commit()
dblock = asyncio.Lock()

def is_member(uid):
    return db.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone() is not None

def reg_open():
    row = db.execute("SELECT value FROM settings WHERE key='reg_open'").fetchone()
    return row is None or row[0] == "1"

def set_reg(v):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('reg_open',?)", ("1" if v else "0",))
    db.commit()

def user_label(name, username):
    return (name or "без имени") + (f" (@{username})" if username else "")

def member_ids():
    return [r[0] for r in db.execute("SELECT user_id FROM users ORDER BY joined_at, user_id")]

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
    rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ───────────────────────── стильное меню на кнопках ─────────────────────────

REPLY_MENU = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="☰ Меню")]],
                                  resize_keyboard=True, is_persistent=True)

MAIN_TEXT = "🏠 <b>Главное меню</b>\n\nВыбери, что сделать 👇"

def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить почту", callback_data="menu:add")],
        [InlineKeyboardButton(text="📋 Ящики", callback_data="menu:list")],
        [InlineKeyboardButton(text="🔔 Категории писем", callback_data="menu:categories")],
        [InlineKeyboardButton(text="👥 Участники", callback_data="menu:users")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help")],
    ])

CANCEL_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="❌ Отмена", callback_data="menu:cancel")]])

HELP_TEXT = ("✨ <b>Mail → Telegram Bot</b>\n\n"
             "Привязывай сколько угодно почтовых ящиков — бот следит за новыми письмами "
             "по IMAP и присылает их сюда.\n\n"
             "🔔 <b>Категории</b> — бот сам определяет тип письма (заявки/одобрения "
             "Monteurzimmer, SMS-коды и т.п.) и для каждого типа можно отдельно включить/"
             "выключить уведомления и показ полного текста.\n\n"
             "👥 <b>Участники</b> — ящики общие для всех участников, уведомления получает каждый. "
             "Любой участник может видеть список, открывать/закрывать вход новым людям и удалять участников.\n\n"
             "🔐 Пароли от почты хранятся в зашифрованном виде.\n"
             f"⏱ Проверка новых писем — раз в {INTERVAL} сек.")

def list_view():
    rows = db.execute("SELECT id, email FROM accounts ORDER BY email").fetchall()
    kb = []
    for id_, em in rows:
        kb.append([InlineKeyboardButton(text=f"🗑 {em}", callback_data=f"rm:{id_}")])
    kb.append([InlineKeyboardButton(text="➕ Добавить ещё", callback_data="menu:add")])
    kb.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")])
    text = ("📋 <b>Привязанные ящики</b>\n\nЖми 🗑, чтобы отвязать ящик."
            if rows else "📋 Пока ничего не привязано.\n\nНажми «➕ Добавить почту».")
    return text, InlineKeyboardMarkup(inline_keyboard=kb)

def allowed_cb(cq: CallbackQuery) -> bool:
    return is_member(cq.from_user.id)

def users_view(me):
    rows = db.execute("SELECT user_id, name, username FROM users ORDER BY joined_at, user_id").fetchall()
    kb, lines = [], []
    for uid, name, un in rows:
        label = user_label(name, un) if (name or un) else f"ID {uid}"
        lines.append("• " + html.escape(label) + (" — <b>это ты</b>" if uid == me else ""))
        kb.append([InlineKeyboardButton(text=("🚪 Выйти (это ты)" if uid == me else f"🗑 {label}"),
                                         callback_data=f"usr:{uid}")])
    opened = reg_open()
    kb.append([InlineKeyboardButton(
        text=("🔓 Регистрация открыта — закрыть" if opened else "🔒 Регистрация закрыта — открыть"),
        callback_data="reg:toggle")])
    kb.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")])
    text = (f"👥 <b>Участники</b> ({len(rows)})\n\n" + "\n".join(lines) + "\n\n" +
            ("🔓 Новые участники могут зайти сами через /start." if opened else
             "🔒 Регистрация закрыта: новые участники не смогут зайти.") +
            "\n\nУчастники делят общие ящики и получают уведомления; настройки категорий у каждого свои.")
    return text, InlineKeyboardMarkup(inline_keyboard=kb)

class Add(StatesGroup):
    email = State(); host = State(); pw = State()

dp = Dispatcher(storage=MemoryStorage())
bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

def allowed(m: Message) -> bool:
    return is_member(m.from_user.id)

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
        for id_, _owner, em, pw, host, last in rows:
            try:
                msgs, newlast = await asyncio.to_thread(fetch_new, host, em, FERNET.decrypt(pw.encode()).decode(), last)
                for u, name, addr, subj, date, body, files in msgs:
                    cat = classify(subj, body)
                    head = (f"📬 <b>Новое письмо</b> → {html.escape(em)}\n"
                            f"<b>От:</b> {html.escape(name)} &lt;{html.escape(addr)}&gt;\n"
                            f"<b>Тема:</b> {html.escape(subj)}\n<b>Дата:</b> {html.escape(date)}\n")
                    if cat != "other": head += f"<b>Тип:</b> {html.escape(CATEGORIES[cat])}\n"
                    if files: head += "📎 " + html.escape(", ".join(files)) + "\n"
                    for uid in member_ids():
                        notify, show_body = get_pref(uid, cat)
                        if not notify:
                            continue
                        text = head + ("\n" + html.escape(body) if body and show_body else "")
                        try:
                            await bot.send_message(uid, text[:4000])
                        except Exception as e:
                            log.warning("send to %s: %s", uid, e)
                if newlast != last:
                    db.execute("UPDATE accounts SET last_uid=? WHERE id=?", (newlast, id_)); db.commit()
            except Exception as e:
                log.warning("poll %s: %s", em, e)

@dp.message(Command("start"))
async def start(m: Message, state: FSMContext):
    await state.clear()
    u = m.from_user
    if not is_member(u.id):
        if not reg_open():
            return await m.answer("🔒 Вход закрыт. Попроси любого участника открыть регистрацию "
                                  "в разделе «👥 Участники», потом снова нажми /start.")
        db.execute("INSERT INTO users(user_id,name,username) VALUES(?,?,?)",
                   (u.id, u.full_name, u.username or ""))
        db.commit()
        for other in member_ids():
            if other != u.id:
                try:
                    await bot.send_message(other, f"👤 Новый участник: <b>{html.escape(user_label(u.full_name, u.username))}</b>")
                except Exception as e:
                    log.warning("notify join %s: %s", other, e)
    else:
        db.execute("UPDATE users SET name=?, username=? WHERE user_id=?", (u.full_name, u.username or "", u.id))
        db.commit()
    await m.answer(HELP_TEXT, reply_markup=REPLY_MENU)
    await m.answer(MAIN_TEXT, reply_markup=main_menu_kb())

@dp.message(F.text == "☰ Меню")
async def menu_button(m: Message, state: FSMContext):
    if not allowed(m): return
    await state.clear()
    await m.answer(MAIN_TEXT, reply_markup=main_menu_kb())

@dp.callback_query(F.data == "noop")
async def cat_noop(cq: CallbackQuery):
    await cq.answer()

@dp.callback_query(F.data.startswith("catnotify:") | F.data.startswith("catbody:"))
async def cat_toggle(cq: CallbackQuery):
    if not allowed_cb(cq): return await cq.answer()
    action, cat = cq.data.split(":", 1)
    toggle_pref(cq.message.chat.id, cat, "notify" if action == "catnotify" else "body")
    await cq.message.edit_reply_markup(reply_markup=categories_kb(cq.message.chat.id))
    await cq.answer("Обновлено")

@dp.callback_query(F.data == "menu:main")
async def menu_main(cq: CallbackQuery, state: FSMContext):
    if not allowed_cb(cq): return await cq.answer()
    await state.clear()
    await cq.message.edit_text(MAIN_TEXT, reply_markup=main_menu_kb())
    await cq.answer()

@dp.callback_query(F.data == "menu:help")
async def menu_help(cq: CallbackQuery):
    if not allowed_cb(cq): return await cq.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")]])
    await cq.message.edit_text(HELP_TEXT, reply_markup=kb)
    await cq.answer()

@dp.callback_query(F.data == "menu:categories")
async def menu_categories(cq: CallbackQuery):
    if not allowed_cb(cq): return await cq.answer()
    await cq.message.edit_text("🔔 <b>Категории писем</b>\n\nЖми на кнопки, чтобы переключить:",
                               reply_markup=categories_kb(cq.message.chat.id))
    await cq.answer()

@dp.callback_query(F.data == "menu:list")
async def menu_list(cq: CallbackQuery):
    if not allowed_cb(cq): return await cq.answer()
    text, kb = list_view()
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()

@dp.callback_query(F.data == "menu:users")
async def menu_users(cq: CallbackQuery):
    if not allowed_cb(cq): return await cq.answer()
    text, kb = users_view(cq.from_user.id)
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()

@dp.callback_query(F.data == "reg:toggle")
async def reg_toggle(cq: CallbackQuery):
    if not allowed_cb(cq): return await cq.answer()
    set_reg(not reg_open())
    text, kb = users_view(cq.from_user.id)
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer("Регистрация открыта" if reg_open() else "Регистрация закрыта")

@dp.callback_query(F.data.startswith("usr:"))
async def user_rm_ask(cq: CallbackQuery):
    if not allowed_cb(cq): return await cq.answer()
    uid = int(cq.data.split(":", 1)[1])
    row = db.execute("SELECT name, username FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row:
        await cq.answer("Уже удалён")
        text, kb = users_view(cq.from_user.id)
        return await cq.message.edit_text(text, reply_markup=kb)
    me = uid == cq.from_user.id
    if me and len(member_ids()) == 1:
        return await cq.answer("Ты единственный участник — выйти нельзя", show_alert=True)
    label = html.escape(user_label(*row) if (row[0] or row[1]) else f"ID {uid}")
    q = ("Выйти из бота? Уведомления приходить перестанут." if me else
         f"Удалить участника <b>{label}</b>?\nОн перестанет получать уведомления"
         + ("." if not reg_open() else ", но сможет зайти снова, пока регистрация открыта."))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"usryes:{uid}"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="menu:users")]])
    await cq.message.edit_text(q, reply_markup=kb)
    await cq.answer()

@dp.callback_query(F.data.startswith("usryes:"))
async def user_rm_yes(cq: CallbackQuery):
    if not allowed_cb(cq): return await cq.answer()
    uid = int(cq.data.split(":", 1)[1])
    me = uid == cq.from_user.id
    if me and len(member_ids()) == 1:
        return await cq.answer("Ты единственный участник — выйти нельзя", show_alert=True)
    db.execute("DELETE FROM users WHERE user_id=?", (uid,))
    db.execute("DELETE FROM category_prefs WHERE chat_id=?", (uid,))
    db.commit()
    if me:
        await cq.message.edit_text("🚪 Ты вышел из бота. Чтобы вернуться, нажми /start (если регистрация открыта).")
        return await cq.answer("Вышел")
    try:
        await bot.send_message(uid, "🚫 Тебя удалили из участников бота.")
    except Exception as e:
        log.warning("notify removal %s: %s", uid, e)
    text, kb = users_view(cq.from_user.id)
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer("Удалён")

@dp.callback_query(F.data == "menu:cancel")
async def menu_cancel(cq: CallbackQuery, state: FSMContext):
    if not allowed_cb(cq): return await cq.answer()
    await state.clear()
    await cq.message.edit_text(MAIN_TEXT, reply_markup=main_menu_kb())
    await cq.answer("Отменено")

@dp.callback_query(F.data == "menu:add")
async def menu_add(cq: CallbackQuery, state: FSMContext):
    if not allowed_cb(cq): return await cq.answer()
    await state.set_state(Add.email)
    await cq.message.edit_text("✉️ Напиши адрес почты, например <code>name@gmail.com</code>",
                               reply_markup=CANCEL_KB)
    await cq.answer()

@dp.callback_query(F.data.startswith("rm:"))
async def menu_rm_ask(cq: CallbackQuery):
    if not allowed_cb(cq): return await cq.answer()
    id_ = int(cq.data.split(":", 1)[1])
    row = db.execute("SELECT email FROM accounts WHERE id=?", (id_,)).fetchone()
    if not row:
        await cq.answer("Уже удалено");
        text, kb = list_view()
        return await cq.message.edit_text(text, reply_markup=kb)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"rmyes:{id_}"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="menu:list")]])
    await cq.message.edit_text(f"Отвязать <b>{html.escape(row[0])}</b>?\nУведомления из этого ящика больше не "
                               f"будут приходить.", reply_markup=kb)
    await cq.answer()

@dp.callback_query(F.data.startswith("rmyes:"))
async def menu_rm_yes(cq: CallbackQuery):
    if not allowed_cb(cq): return await cq.answer()
    id_ = int(cq.data.split(":", 1)[1])
    db.execute("DELETE FROM accounts WHERE id=?", (id_,)); db.commit()
    text, kb = list_view()
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer("Отвязано")

@dp.message(Add.email)
async def add_email(m: Message, state: FSMContext):
    if not allowed(m): return await state.clear()
    em = (m.text or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", em):
        return await m.answer("Не похоже на адрес почты. Попробуй ещё раз.", reply_markup=CANCEL_KB)
    if db.execute("SELECT 1 FROM accounts WHERE email=?", (em,)).fetchone():
        await state.clear()
        await m.answer(f"Ящик <b>{html.escape(em)}</b> уже привязан — он общий для всех участников.")
        return await m.answer(MAIN_TEXT, reply_markup=main_menu_kb())
    dom = em.split("@")[1]
    await state.update_data(email=em)
    if dom in HOSTS:
        await state.update_data(host=HOSTS[dom]); await state.set_state(Add.pw)
        await m.answer(HELP_PASS.get(dom, "Нужен пароль приложения (не основной пароль).") +
                       "\n\nОтправь пароль приложения. Сообщение с ним я сразу удалю.", reply_markup=CANCEL_KB)
    else:
        guess = f"imap.{dom}"
        try:
            await asyncio.to_thread(socket.gethostbyname, guess)
            await state.update_data(host=guess); await state.set_state(Add.pw)
            await m.answer(f"Нашёл IMAP-сервер <code>{html.escape(guess)}</code>.\n\nОтправь пароль "
                           "(лучше пароль приложения). Сообщение с ним я сразу удалю.", reply_markup=CANCEL_KB)
        except OSError:
            await state.set_state(Add.host)
            await m.answer(f"Не знаю IMAP-сервер для {html.escape(dom)}. Напиши его, например "
                           f"<code>imap.{html.escape(dom)}</code>", reply_markup=CANCEL_KB)

@dp.message(Add.host)
async def add_host(m: Message, state: FSMContext):
    if not allowed(m): return await state.clear()
    await state.update_data(host=(m.text or "").strip()); await state.set_state(Add.pw)
    await m.answer("Отправь пароль (лучше пароль приложения). Сообщение с ним я сразу удалю.",
                   reply_markup=CANCEL_KB)

@dp.message(Add.pw)
async def add_pw(m: Message, state: FSMContext):
    if not allowed(m): return await state.clear()
    d = await state.get_data(); pw = (m.text or "").strip()
    try: await m.delete()
    except Exception: pass
    try:
        def check():
            c = imap_connect(d["host"], d["email"], pw)
            u = max_uid(c); c.logout(); return u
        uid = await asyncio.to_thread(check)
    except Exception as e:
        await state.set_state(Add.pw)
        return await m.answer(f"❌ Не удалось войти: <code>{html.escape(str(e))[:300]}</code>\n"
                              "Проверь, что включён IMAP и используется пароль приложения. Пришли пароль ещё раз.",
                              reply_markup=CANCEL_KB)
    db.execute("INSERT OR IGNORE INTO accounts(chat_id,email,pw,host,last_uid) VALUES(?,?,?,?,?)",
               (m.chat.id, d["email"], FERNET.encrypt(pw.encode()).decode(), d["host"], uid))
    db.commit(); await state.clear()
    await m.answer(f"✅ <b>{html.escape(d['email'])}</b> привязана. Старые письма не придут, только новые "
                   f"(проверка раз в {INTERVAL} сек).")
    await m.answer(MAIN_TEXT, reply_markup=main_menu_kb())

async def main():
    await bot.set_my_commands([BotCommand(command="start", description="Открыть меню")])
    asyncio.create_task(poll())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
