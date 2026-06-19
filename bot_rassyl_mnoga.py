"""
📤 Broadcast Bot — мультипользовательский рассыльщик
Каждый пользователь имеет свои аккаунты и рассылки.
Доступ только для подписчиков шоп-бота.

pip install python-telegram-bot==20.7 telethon pandas psycopg2-binary
python bot.py
"""

import asyncio, csv, io, logging, os, re
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
)
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    PhoneCodeExpiredError, PasswordHashInvalidError, FloodWaitError,
)

import config, db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SESSION_DIR = config.SESSION_DIR
os.makedirs(SESSION_DIR, exist_ok=True)

# ── States ────────────────────────────────────────────────────────────────────
(
    MAIN_MENU,
    ACCOUNTS_MENU,
    WAIT_PHONE,
    WAIT_CODE,
    WAIT_2FA,
    EDIT_TEXT,
    WAIT_CSV,
    SET_DELAY,
    BROADCASTING,
) = range(9)

# ── Telethon клиенты в памяти { user_id: { phone: TelegramClient } } ─────────
clients: dict[int, dict[str, TelegramClient]] = {}

# ── Текущие настройки рассылки для каждого юзера ─────────────────────────────
user_state: dict[int, dict] = {}

def get_state(user_id: int) -> dict:
    if user_id not in user_state:
        user_state[user_id] = {
            "broadcast_text": "Привет! Это тестовое сообщение.",
            "delay": 5,
            "contacts": [],
            "stop_broadcast": False,
            "_auth_phone": None,
        }
    return user_state[user_id]

# ── Subscription guard ────────────────────────────────────────────────────────

def sub_required(func):
    """Декоратор — проверяет подписку перед каждым действием."""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not db.is_subscribed(user_id):
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Купить доступ", url="https://t.me/m16el1n0_shopbot")
            ]])
            msg = update.message or update.callback_query.message
            await msg.reply_text(
                "❌ У тебя нет активной подписки.\n\nКупи доступ в магазине:",
                reply_markup=kb
            )
            return ConversationHandler.END
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper

# ── Telethon helpers ──────────────────────────────────────────────────────────

def session_path(user_id: int, phone: str) -> str:
    """Путь к файлу сессии конкретного пользователя и аккаунта."""
    safe_phone = re.sub(r"\D", "", phone)
    return os.path.join(SESSION_DIR, f"{user_id}_{safe_phone}")

async def get_client(user_id: int, phone: str) -> TelegramClient:
    """Возвращает подключённый клиент из кэша или создаёт новый."""
    if user_id not in clients:
        clients[user_id] = {}
    if phone not in clients[user_id]:
        client = TelegramClient(
            session_path(user_id, phone),
            config.API_ID,
            config.API_HASH,
        )
        await client.connect()
        clients[user_id][phone] = client
    return clients[user_id][phone]

async def active_client(user_id: int) -> TelegramClient | None:
    phone = db.get_active_phone(user_id)
    if not phone:
        return None
    return await get_client(user_id, phone)

def active_label(user_id: int) -> str:
    phone = db.get_active_phone(user_id)
    if not phone:
        return "—"
    accounts = db.get_accounts(user_id)
    for a in accounts:
        if a["phone"] == phone:
            return a["label"] or phone
    return phone

# ── CSV parser ────────────────────────────────────────────────────────────────

def extract_tg_contacts(csv_bytes: bytes) -> list[str]:
    try:
        import pandas as pd
    except ImportError:
        pd = None

    contacts = []
    seen = set()

    PHONE_COLS = ["телефон 1","телефон1","phone 1","phone1","телефон","phone"]
    TG_COLS    = ["telegram","тelegram","tg","телеграм"]

    def add_phone(val: str):
        digits = re.sub(r"\D", "", val)
        if len(digits) >= 10:
            if digits.startswith("8") and len(digits) == 11:
                digits = "7" + digits[1:]
            if digits not in seen:
                seen.add(digits)
                contacts.append("+" + digits)

    def add_tg(val: str):
        val = val.strip()
        tme = re.search(r"t(?:elegram)?\.me/([^\s/]+)", val, re.I)
        if tme:
            slug = tme.group(1)
            if slug.startswith("+"):
                add_phone(slug)
            else:
                key = slug.lower()
                if key not in seen:
                    seen.add(key)
                    contacts.append("@" + slug)
            return
        m = re.match(r"@([A-Za-z0-9_]{5,32})$", val)
        if m:
            key = m.group(1).lower()
            if key not in seen:
                seen.add(key)
                contacts.append("@" + m.group(1))
            return
        if re.search(r"\d{7,}", val):
            add_phone(val)

    if pd:
        try:
            text_io = io.StringIO(csv_bytes.decode("utf-8", errors="replace"))
            df = pd.read_csv(text_io, sep=None, engine="python", dtype=str)
            df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]
            cols_lower = {c.lower(): c for c in df.columns}
            for col_lower, col_real in cols_lower.items():
                if col_lower in PHONE_COLS:
                    for val in df[col_real].dropna():
                        add_phone(str(val))
                elif col_lower in TG_COLS:
                    for val in df[col_real].dropna():
                        add_tg(str(val))
            if contacts:
                return contacts
        except Exception:
            pass

    # fallback
    username_re = re.compile(r"@([A-Za-z0-9_]{5,32})")
    tme_re      = re.compile(r"(?:t(?:elegram)?\.me)/([A-Za-z0-9_]{5,32})", re.I)
    phone_re    = re.compile(r"(\+?[78][\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})")
    text = csv_bytes.decode("utf-8", errors="replace")
    for delim in (",", ";", "\t", "|"):
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        for row in reader:
            for cell in row:
                cell = cell.strip()
                for m in username_re.findall(cell):
                    key = "@" + m.lower()
                    if key not in seen:
                        seen.add(key)
                        contacts.append("@" + m)
                for m in tme_re.findall(cell):
                    key = "@" + m.lower()
                    if key not in seen:
                        seen.add(key)
                        contacts.append("@" + m)
                for m in phone_re.findall(cell):
                    digits = re.sub(r"\D", "", m)
                    if digits not in seen:
                        seen.add(digits)
                        contacts.append("+" + digits)
        if contacts:
            break

    return contacts

# ── Keyboards ─────────────────────────────────────────────────────────────────

def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    label = active_label(user_id)
    acc_btn = InlineKeyboardButton(
        f"👤 Аккаунт: {label}" if label != "—" else "🔑 Добавить аккаунт",
        callback_data="accounts"
    )
    return InlineKeyboardMarkup([
        [acc_btn],
        [InlineKeyboardButton("✏️ Изменить текст рассылки", callback_data="edit_text")],
        [InlineKeyboardButton("📤 Разослать",               callback_data="broadcast")],
        [InlineKeyboardButton("⏱ Настройки КД",            callback_data="set_delay")],
    ])

def accounts_kb(user_id: int) -> InlineKeyboardMarkup:
    accounts = db.get_accounts(user_id)
    active   = db.get_active_phone(user_id)
    kb = []
    for a in accounts:
        marker = "✅ " if a["phone"] == active else ""
        kb.append([InlineKeyboardButton(
            f"{marker}{a['label']} ({a['phone']})",
            callback_data=f"switch_{a['phone']}"
        )])
    kb.append([InlineKeyboardButton("➕ Добавить аккаунт", callback_data="auth")])
    if active:
        kb.append([InlineKeyboardButton("🗑 Удалить активный", callback_data="del_account")])
    kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back")])
    return InlineKeyboardMarkup(kb)

async def send_main_menu(update: Update, text: str = "Главное меню:") -> None:
    user_id = update.effective_user.id
    msg = update.message or update.callback_query.message
    await msg.reply_text(text, reply_markup=main_menu_kb(user_id))

# ── /start ────────────────────────────────────────────────────────────────────

@sub_required
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await send_main_menu(update, "👋 Привет! Главное меню:")
    return MAIN_MENU

# ── Callback router ───────────────────────────────────────────────────────────

@sub_required
async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q       = update.callback_query
    await q.answer()
    data    = q.data
    user_id = update.effective_user.id

    if data == "accounts":
        await q.message.reply_text("👤 Твои аккаунты:", reply_markup=accounts_kb(user_id))
        return ACCOUNTS_MENU

    if data == "back":
        await send_main_menu(update)
        return MAIN_MENU

    if data == "edit_text":
        state = get_state(user_id)
        await q.message.reply_text(
            f"📝 Текущий текст:\n\n{state['broadcast_text']}\n\nВведи новый:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]]),
        )
        return EDIT_TEXT

    if data == "broadcast":
        client = await active_client(user_id)
        if client is None or not client.is_connected():
            await q.message.reply_text("⚠️ Сначала добавь аккаунт!", reply_markup=main_menu_kb(user_id))
            return MAIN_MENU
        await q.message.reply_text(
            f"📂 Отправь CSV файл с контактами.\nАккаунт: {active_label(user_id)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]]),
        )
        return WAIT_CSV

    if data == "set_delay":
        state = get_state(user_id)
        await q.message.reply_text(
            f"⏱ Текущий КД: {state['delay']} сек.\n\nВведи новое значение:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]]),
        )
        return SET_DELAY

    if data == "auth":
        await q.message.reply_text(
            "📱 Введи номер телефона аккаунта в формате +79991234567:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]]),
        )
        return WAIT_PHONE

    if data.startswith("switch_"):
        phone = data[7:]
        db.set_active_phone(user_id, phone)
        await send_main_menu(update, f"✅ Активный аккаунт: {phone}")
        return MAIN_MENU

    if data == "del_account":
        phone = db.get_active_phone(user_id)
        if phone:
            db.remove_account(user_id, phone)
            # отключаем клиент
            if user_id in clients and phone in clients[user_id]:
                await clients[user_id][phone].disconnect()
                del clients[user_id][phone]
            db.execute("DELETE FROM active_account WHERE user_id=?", (user_id,))
        await send_main_menu(update, "🗑 Аккаунт удалён.")
        return MAIN_MENU

    if data == "stop_broadcast":
        get_state(user_id)["stop_broadcast"] = True
        await q.answer("🛑 Останавливаю...")
        return BROADCASTING

    return MAIN_MENU

# ── Auth flow ─────────────────────────────────────────────────────────────────

async def wait_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    phone   = update.message.text.strip()
    state   = get_state(user_id)
    state["_auth_phone"] = phone

    client = TelegramClient(session_path(user_id, phone), config.API_ID, config.API_HASH)
    await client.connect()

    if user_id not in clients:
        clients[user_id] = {}
    clients[user_id]["_pending"] = client

    try:
        await client.send_code_request(phone)
        await update.message.reply_text("📩 Код отправлен. Введи код из Telegram:")
        return WAIT_CODE
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return MAIN_MENU

async def wait_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    code    = update.message.text.strip()
    state   = get_state(user_id)
    phone   = state["_auth_phone"]
    client  = clients[user_id]["_pending"]

    try:
        me = await client.sign_in(phone, code)
        label = f"@{me.username}" if me.username else me.first_name or phone
        db.add_account(user_id, phone, label)
        db.set_active_phone(user_id, phone)
        clients[user_id][phone] = client
        del clients[user_id]["_pending"]
        await send_main_menu(update, f"✅ Аккаунт {label} добавлен!")
        return MAIN_MENU
    except SessionPasswordNeededError:
        await update.message.reply_text("🔐 Введи пароль двухфакторной аутентификации:")
        return WAIT_2FA
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        await update.message.reply_text("❌ Неверный или просроченный код. Попробуй ещё раз:")
        return WAIT_CODE

async def wait_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id  = update.effective_user.id
    password = update.message.text.strip()
    state    = get_state(user_id)
    phone    = state["_auth_phone"]
    client   = clients[user_id]["_pending"]

    try:
        me = await client.sign_in(password=password)
        label = f"@{me.username}" if me.username else me.first_name or phone
        db.add_account(user_id, phone, label)
        db.set_active_phone(user_id, phone)
        clients[user_id][phone] = client
        del clients[user_id]["_pending"]
        await send_main_menu(update, f"✅ Аккаунт {label} добавлен!")
        return MAIN_MENU
    except PasswordHashInvalidError:
        await update.message.reply_text("❌ Неверный пароль. Попробуй ещё:")
        return WAIT_2FA

# ── Edit text ─────────────────────────────────────────────────────────────────

async def save_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    get_state(user_id)["broadcast_text"] = update.message.text.strip()
    await send_main_menu(update, "✅ Текст сохранён!")
    return MAIN_MENU

# ── Set delay ─────────────────────────────────────────────────────────────────

async def save_delay(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    text    = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("❌ Только целое число >= 1:")
        return SET_DELAY
    get_state(user_id)["delay"] = int(text)
    await send_main_menu(update, f"✅ КД: {int(text)} сек.")
    return MAIN_MENU

# ── CSV + broadcast ───────────────────────────────────────────────────────────

async def handle_csv(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    doc     = update.message.document
    if not doc:
        await update.message.reply_text("❌ Отправь CSV файл.")
        return WAIT_CSV

    file      = await doc.get_file()
    csv_bytes = await file.download_as_bytearray()
    contacts  = extract_tg_contacts(bytes(csv_bytes))

    if not contacts:
        await update.message.reply_text("❌ Контактов не найдено. Жду CSV с @username или номерами.")
        return WAIT_CSV

    state = get_state(user_id)
    state["contacts"] = contacts

    await update.message.reply_text(
        f"✅ Найдено: {len(contacts)}\n"
        f"Первые 5: {', '.join(contacts[:5])}\n\n"
        f"👤 Аккаунт: {active_label(user_id)}\n"
        f"📝 Текст: {state['broadcast_text'][:80]}...\n"
        f"⏱ КД: {state['delay']} сек.\n\n"
        f"Начать рассылку?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Да, начать", callback_data="confirm_broadcast")],
            [InlineKeyboardButton("◀️ Отмена",     callback_data="back")],
        ]),
    )
    return BROADCASTING

async def confirm_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q       = update.callback_query
    await q.answer()
    user_id = update.effective_user.id

    if q.data == "back":
        await send_main_menu(update)
        return MAIN_MENU

    state    = get_state(user_id)
    client   = await active_client(user_id)
    contacts = state["contacts"]
    text     = state["broadcast_text"]
    delay    = state["delay"]
    total    = len(contacts)
    phone    = db.get_active_phone(user_id)

    state["stop_broadcast"] = False

    stop_kb      = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 СТОП", callback_data="stop_broadcast")]])
    progress_msg = await q.message.reply_text(f"📤 Рассылка запущена\n0 / {total}", reply_markup=stop_kb)

    ok, fail, errors = 0, 0, []

    for i, contact in enumerate(contacts, 1):
        if state["stop_broadcast"]:
            break
        try:
            await client.send_message(contact, text)
            ok += 1
        except FloodWaitError as e:
            errors.append(f"FloodWait {e.seconds}s")
            await asyncio.sleep(e.seconds)
            try:
                await client.send_message(contact, text)
                ok += 1
            except Exception:
                fail += 1
        except Exception as e:
            fail += 1
            errors.append(f"{contact}: {str(e)[:50]}")

        if i % 5 == 0 or i == total:
            try:
                await progress_msg.edit_text(
                    f"📤 Рассылка...\n{i} / {total} | ✅ {ok} | ❌ {fail}",
                    reply_markup=stop_kb,
                )
            except Exception:
                pass

        if i < total and not state["stop_broadcast"]:
            await asyncio.sleep(delay)

    try:
        await progress_msg.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    db.log_broadcast(user_id, phone, total, ok, fail)

    stopped = state["stop_broadcast"]
    result  = f"{'🛑 Остановлено' if stopped else '✅ Готово!'}\nОтправлено: {ok}/{total}\nОшибок: {fail}"
    if errors:
        result += "\n\nПоследние ошибки:\n" + "\n".join(errors[-3:])

    await send_main_menu(update, result)
    return MAIN_MENU

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    db.db_init()

    app = Application.builder().token(config.BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MAIN_MENU:     [CallbackQueryHandler(callback_router)],
            ACCOUNTS_MENU: [CallbackQueryHandler(callback_router)],
            WAIT_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wait_phone),
                CallbackQueryHandler(callback_router),
            ],
            WAIT_CODE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, wait_code)],
            WAIT_2FA:   [MessageHandler(filters.TEXT & ~filters.COMMAND, wait_2fa)],
            EDIT_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_text),
                CallbackQueryHandler(callback_router),
            ],
            WAIT_CSV: [
                MessageHandler(filters.Document.ALL, handle_csv),
                CallbackQueryHandler(callback_router),
            ],
            BROADCASTING: [CallbackQueryHandler(confirm_broadcast)],
            SET_DELAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_delay),
                CallbackQueryHandler(callback_router),
            ],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        per_message=False,
    )

    app.add_handler(conv)
    logger.info("🚀 Broadcast Bot запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
