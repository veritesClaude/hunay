import asyncio, imaplib, email, os, re, socket, sqlite3, html, logging, json, time
import urllib.request, urllib.parse, urllib.error
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
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "").strip()
MS_TENANT = os.getenv("MS_TENANT", "consumers").strip() or "consumers"
MS_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
MS_DOMAINS = {"outlook.com", "outlook.de", "hotmail.com", "hotmail.de", "live.com", "live.de", "msn.com"}
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
    "gmx.ch": "imap.gmx.net", "gmx.com": "imap.gmx.net", "mein.gmx": "imap.gmx.net",
    "gmx.eu": "imap.gmx.net", "gmx.li": "imap.gmx.net", "gmx.fr": "imap.gmx.net", "gmx.es": "imap.gmx.net",
    "outlook.de": "outlook.office365.com", "hotmail.de": "outlook.office365.com",
    "live.com": "outlook.office365.com", "live.de": "outlook.office365.com", "msn.com": "outlook.office365.com",
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
for _d in ("gmx.de", "gmx.net", "gmx.at", "gmx.ch", "gmx.com", "mein.gmx", "gmx.eu", "gmx.li", "gmx.fr", "gmx.es"):
    HELP_PASS[_d] = _GMX
_OUTLOOK = ("Outlook/Hotmail: включи двухэтапную проверку и создай пароль приложения "
            "(account.microsoft.com → Безопасность → Дополнительные параметры безопасности), "
            "IMAP должен быть включён. Если Microsoft не пускает по паролю — это их ограничение "
            "на вход без OAuth, а не ошибка бота: настрой вход через Microsoft (MS_CLIENT_ID, см. README) "
            "или пересылку писем на GMX.")
for _d in ("outlook.com", "outlook.de", "hotmail.com", "hotmail.de", "live.com", "live.de", "msn.com"):
    HELP_PASS[_d] = _OUTLOOK
HELP_PASS["web.de"] = _GMX.replace("GMX", "WEB.DE").replace("GMX Postfach", "WEB.DE Postfach")

db = sqlite3.connect(DB, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS accounts(
 id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, email TEXT, pw TEXT,
 host TEXT, last_uid INTEGER DEFAULT 0, UNIQUE(chat_id,email))""")
try:
    db.execute("ALTER TABLE accounts ADD COLUMN auth TEXT DEFAULT 'password'")
except sqlite3.OperationalError:
    pass
db.execute("""CREATE TABLE IF NOT EXISTS category_prefs(
 chat_id INTEGER, category TEXT, notify INTEGER DEFAULT 1, body INTEGER DEFAULT 1,
 PRIMARY KEY(chat_id,category))""")
db.execute("""CREATE TABLE IF NOT EXISTS users(
 user_id INTEGER PRIMARY KEY, name TEXT DEFAULT '', username TEXT DEFAULT '',
 joined_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
try:
    db.execute("ALTER TABLE users ADD COLUMN hidden INTEGER DEFAULT 0")
except sqlite3.OperationalError:
    pass
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
    rows = db.execute("SELECT user_id, name, username FROM users WHERE COALESCE(hidden,0)=0 "
                      "ORDER BY joined_at, user_id").fetchall()
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
    email = State(); host = State(); pw = State(); oauth = State()

dp = Dispatcher(storage=MemoryStorage())
bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

def allowed(m: Message) -> bool:
    return is_member(m.from_user.id)

def dec(v):
    try: return str(make_header(decode_header(v or "")))
    except Exception: return v or ""

def imap_connect(host, user, secret, oauth=False):
    c = imaplib.IMAP4_SSL(host, 993, timeout=30)
    if oauth:
        c.authenticate("XOAUTH2", lambda _: f"user={user}\x01auth=Bearer {secret}\x01\x01".encode())
    else:
        c.login(user, secret)
    c.select("INBOX", readonly=True)
    return c

def ms_post(path, data):
    req = urllib.request.Request(f"https://login.microsoftonline.com/{MS_TENANT}/oauth2/v2.0/{path}",
                                  data=urllib.parse.urlencode(data).encode(),
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try: return json.load(e)
        except Exception: return {"error": f"http_{e.code}"}
    except Exception as e:
        return {"error": "network", "error_description": str(e)}

_ms_cache = {}

def ms_access(id_, refresh_token):
    tok, exp = _ms_cache.get(id_, (None, 0))
    if tok and exp > time.time() + 120:
        return tok, refresh_token
    r = ms_post("token", {"client_id": MS_CLIENT_ID, "grant_type": "refresh_token",
                          "refresh_token": refresh_token, "scope": MS_SCOPE})
    if "access_token" not in r:
        raise RuntimeError(f"Microsoft: {r.get('error')}: {str(r.get('error_description', ''))[:150]}")
    _ms_cache[id_] = (r["access_token"], time.time() + int(r.get("expires_in", 3600)))
    return r["access_token"], r.get("refresh_token", refresh_token)

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

def fetch_new(host, user, pw, last_uid, oauth=False):
    c = imap_connect(host, user, pw, oauth)
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
        rows = db.execute("SELECT id,chat_id,email,pw,host,last_uid,COALESCE(auth,'password') FROM accounts").fetchall()
        for id_, _owner, em, pw, host, last, auth in rows:
            try:
                secret = FERNET.decrypt(pw.encode()).decode()
                if auth == "oauth":
                    secret, new_rt = await asyncio.to_thread(ms_access, id_, secret)
                    if FERNET.decrypt(pw.encode()).decode() != new_rt:
                        db.execute("UPDATE accounts SET pw=? WHERE id=?", (FERNET.encrypt(new_rt.encode()).decode(), id_))
                        db.commit()
                msgs, newlast = await asyncio.to_thread(fetch_new, host, em, secret, last, auth == "oauth")
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
    row = db.execute("SELECT name, username FROM users WHERE user_id=? AND COALESCE(hidden,0)=0", (uid,)).fetchone()
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
    if db.execute("SELECT 1 FROM users WHERE user_id=? AND COALESCE(hidden,0)=0", (uid,)).fetchone() is None:
        return await cq.answer("Уже удалён")
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
    if dom in MS_DOMAINS and MS_CLIENT_ID:
        await state.update_data(host=HOSTS[dom])
        return await start_oauth(m, state)
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

async def start_oauth(m: Message, state: FSMContext):
    d = await state.get_data()
    r = await asyncio.to_thread(ms_post, "devicecode", {"client_id": MS_CLIENT_ID, "scope": MS_SCOPE})
    if "device_code" not in r:
        log.warning("devicecode failed: %s", r)
        await state.set_state(Add.pw)
        err = f"{r.get('error')}: {str(r.get('error_description', ''))[:200]}"
        return await m.answer(f"❌ Не удалось начать вход через Microsoft: <code>{html.escape(err)}</code>\n\n"
                              "Можно попробовать пароль приложения — пришли его.", reply_markup=CANCEL_KB)
    code = r["device_code"]
    link = r.get("verification_uri", "https://microsoft.com/link")
    await state.set_state(Add.oauth); await state.update_data(device_code=code)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Открыть страницу Microsoft", url=link)],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu:cancel")]])
    msg = await m.answer(f"🔐 <b>Вход через Microsoft</b>\n\n1️⃣ Открой {html.escape(link)}\n"
                         f"2️⃣ Введи код: <code>{html.escape(r['user_code'])}</code>\n"
                         f"3️⃣ Войди в <b>{html.escape(d['email'])}</b> и нажми «Разрешить».\n\n"
                         f"⏳ Жду подтверждения (код действует {int(r.get('expires_in', 900)) // 60} мин)…",
                         reply_markup=kb)
    asyncio.create_task(oauth_wait(msg, state, m.chat.id, d["email"], d["host"], r))

async def oauth_wait(msg, state, chat_id, em, host, r):
    code, interval = r["device_code"], int(r.get("interval", 5))
    deadline = time.time() + int(r.get("expires_in", 900))
    async def fail(text):
        await state.clear()
        try: await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:main")]]))
        except Exception: pass
    while time.time() < deadline:
        await asyncio.sleep(interval)
        if await state.get_state() != Add.oauth.state or (await state.get_data()).get("device_code") != code:
            return
        t = await asyncio.to_thread(ms_post, "token", {
            "client_id": MS_CLIENT_ID, "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": code})
        err = t.get("error")
        if err == "authorization_pending": continue
        if err == "slow_down": interval += 5; continue
        if "access_token" not in t:
            log.warning("oauth failed %s: %s", em, t)
            return await fail(f"❌ Вход через Microsoft не удался: <code>{html.escape(str(err))}</code>\n"
                              f"{html.escape(str(t.get('error_description', ''))[:200])}")
        try:
            def check():
                c = imap_connect(host, em, t["access_token"], True)
                u = max_uid(c); c.logout(); return u
            uid = await asyncio.wait_for(asyncio.to_thread(check), timeout=60)
        except Exception as e:
            log.warning("oauth imap failed %s: %r", em, e)
            return await fail(f"❌ Вход прошёл, но почта не открылась: <code>{html.escape(str(e) or type(e).__name__)[:300]}</code>\n"
                              "Проверь, что ты вошла именно в этот ящик.")
        cur = db.execute("INSERT OR IGNORE INTO accounts(chat_id,email,pw,host,last_uid,auth) VALUES(?,?,?,?,?,'oauth')",
                         (chat_id, em, FERNET.encrypt(t.get("refresh_token", "").encode()).decode(), host, uid))
        db.commit()
        if cur.lastrowid:
            _ms_cache[cur.lastrowid] = (t["access_token"], time.time() + int(t.get("expires_in", 3600)))
        await state.clear()
        log.info("mailbox added (oauth): %s @ %s", em, host)
        await msg.edit_text(f"✅ <b>{html.escape(em)}</b> привязана через Microsoft. Старые письма не придут, "
                            f"только новые (проверка раз в {INTERVAL} сек).")
        return await bot.send_message(chat_id, MAIN_TEXT, reply_markup=main_menu_kb())
    await fail("⌛ Время на вход вышло. Нажми «➕ Добавить почту» и попробуй снова.")

@dp.message(Add.oauth)
async def add_oauth_wait_msg(m: Message):
    await m.answer("Жду, пока ты войдёшь на странице Microsoft с кодом выше. Или нажми «❌ Отмена» под сообщением.")

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
    if "email" not in d or "host" not in d:
        await state.clear()
        return await m.answer("Данные добавления потерялись (бот перезапускался). Начни заново.",
                              reply_markup=main_menu_kb())
    status = await m.answer(f"⏳ Проверяю вход в <b>{html.escape(d['email'])}</b> ({html.escape(d['host'])})…")
    try:
        def check():
            c = imap_connect(d["host"], d["email"], pw)
            u = max_uid(c); c.logout(); return u
        uid = await asyncio.wait_for(asyncio.to_thread(check), timeout=60)
    except Exception as e:
        log.warning("login failed %s @ %s: %r", d["email"], d["host"], e)
        await state.set_state(Add.pw)
        err = str(e) or type(e).__name__
        return await status.edit_text(
            f"❌ Не удалось войти: <code>{html.escape(err)[:300]}</code>\n"
            "Проверь, что включён доступ по IMAP и используется пароль приложения (если есть двухфакторка). "
            "Пришли пароль ещё раз.", reply_markup=CANCEL_KB)
    db.execute("INSERT OR IGNORE INTO accounts(chat_id,email,pw,host,last_uid) VALUES(?,?,?,?,?)",
               (m.chat.id, d["email"], FERNET.encrypt(pw.encode()).decode(), d["host"], uid))
    db.commit(); await state.clear()
    log.info("mailbox added: %s @ %s", d["email"], d["host"])
    await status.edit_text(f"✅ <b>{html.escape(d['email'])}</b> привязана. Старые письма не придут, только новые "
                           f"(проверка раз в {INTERVAL} сек).")
    await m.answer(MAIN_TEXT, reply_markup=main_menu_kb())

@dp.message()
async def fallback(m: Message):
    if not allowed(m):
        return await m.answer("Нажми /start")
    await m.answer("Не понял 🤔 Нажми «☰ Меню» или выбери действие кнопкой.", reply_markup=main_menu_kb())

@dp.errors()
async def on_error(event):
    log.exception("unhandled error: %r", event.exception)
    upd = event.update
    chat = (upd.message.chat.id if upd.message else
            upd.callback_query.message.chat.id if upd.callback_query and upd.callback_query.message else None)
    if chat:
        try:
            await bot.send_message(chat, f"⚠️ Внутренняя ошибка: <code>{html.escape(repr(event.exception))[:300]}</code>")
        except Exception:
            pass
    return True

async def main():
    await bot.set_my_commands([BotCommand(command="start", description="Открыть меню")])
    asyncio.create_task(poll())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
