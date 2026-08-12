import asyncio
import datetime
import json
import logging
import os
from contextvars import ContextVar

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import (
    Message,
    CallbackQuery,
    PreCheckoutQuery,
    LabeledPrice,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import BaseMiddleware

logging.basicConfig(level=logging.INFO)

# ============================== CONFIG ==============================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID", "0"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.db")

SUBSCRIPTION_STARS = 100
SUBSCRIPTION_DAYS = 30

# ---- редактируемые тексты (ключ -> (админ-команда, подсказка админу, дефолт)) ----

DEFAULT_WELCOME_TEXT = (
    "👋 Привет! Напиши название карты — пришлю пики, "
    "или название бойца — пришлю билд."
)
DEFAULT_HELP_TEXT = (
    "ℹ️ Как пользоваться\n"
    "Напиши название карты — пришлю пики (лучших бойцов на неё).\n"
    "Напиши название бойца — пришлю билд.\n"
    "Например: «Гейзер» или «Шелли».\n\n"
    "💎 Подписка\n"
    "1 день бесплатно при первом запросе, дальше — "
    f"{SUBSCRIPTION_STARS}⭐ / {SUBSCRIPTION_DAYS} дней.\n\n"
    "🆘 Проблемы?\n"
    "Пиши администратору бота."
)
DEFAULT_NO_ACCESS_TEXT = "У вас нет активной подписки. Хотите купить?"
DEFAULT_SEARCH_PROMPT_TEXT = (
    "Напиши название карты — пришлю пики, или название бойца — пришлю билд."
)
DEFAULT_NOT_FOUND_TEXT = "🤔 Не нашёл такую карту или бойца. Проверь написание."
DEFAULT_SUB_ACTIVE_TEXT = "💎 Подписка активна.\nОсталось: {days} дн. {hours} ч."
DEFAULT_REMINDER_TEXT = (
    "⏰ Напоминание: ваша подписка истекает менее чем через сутки. "
    "Не забудьте продлить, чтобы не потерять доступ."
)
DEFAULT_PAYMENT_SUCCESS_TEXT = "✅ Оплата получена! Подписка продлена на {days} дней."
DEFAULT_REFERRAL_TEXT = (
    "👥 Ваша реферальная ссылка:\n{link}\n\nПриглашено пользователей: {count}"
)

EDITABLE_TEXTS = {
    "welcome_text": ("setwelcome", "Пришли новый текст приветствия (/start).", DEFAULT_WELCOME_TEXT),
    "help_text": ("sethelp", 'Пришли новый текст для раздела "Как пользоваться".', DEFAULT_HELP_TEXT),
    "no_access_text": ("setnoaccess", "Пришли новый текст сообщения об отсутствии подписки.", DEFAULT_NO_ACCESS_TEXT),
    "search_prompt_text": ("setsearchprompt", "Пришли новый текст подсказки поиска.", DEFAULT_SEARCH_PROMPT_TEXT),
    "not_found_text": ("setnotfound", 'Пришли новый текст сообщения "не найдено".', DEFAULT_NOT_FOUND_TEXT),
    "sub_active_text": (
        "setsubactive",
        "Пришли новый текст статуса активной подписки. Можно использовать {days} и {hours}.",
        DEFAULT_SUB_ACTIVE_TEXT,
    ),
    "reminder_text": ("setreminder", "Пришли новый текст напоминания об истечении подписки.", DEFAULT_REMINDER_TEXT),
    "payment_success_text": (
        "setpaymentsuccess",
        "Пришли новый текст сообщения об успешной оплате. Можно использовать {days}.",
        DEFAULT_PAYMENT_SUCCESS_TEXT,
    ),
    "referral_text": (
        "setreferral",
        'Пришли новый текст раздела "Мои рефералы". Можно использовать {link} и {count}.',
        DEFAULT_REFERRAL_TEXT,
    ),
}

# ============================== DATABASE ==============================

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    joined_at TEXT NOT NULL,
    trial_used INTEGER NOT NULL DEFAULT 0,
    sub_until TEXT,
    reminder_sent INTEGER NOT NULL DEFAULT 0,
    referred_by INTEGER
);
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS maps (
    search_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS builds (
    search_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_type TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_deletes (
    admin_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    search_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_inputs (
    admin_id INTEGER PRIMARY KEY,
    action TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_state (
    user_id INTEGER PRIMARY KEY,
    welcome_msg_id INTEGER,
    last_user_msg_id INTEGER,
    last_bot_msg_ids TEXT
);
"""


async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(SCHEMA)
        for key, (_, _, default) in EDITABLE_TEXTS.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, default)
            )
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("subscription_enabled", "1"),
        )
        if MAIN_ADMIN_ID:
            await db.execute(
                "INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (MAIN_ADMIN_ID,)
            )
        await db.commit()


def now_iso():
    return datetime.datetime.utcnow().isoformat()


async def get_setting(key):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


async def set_setting(key, value):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def is_subscription_enabled():
    return await get_setting("subscription_enabled") == "1"


async def is_admin(user_id):
    if user_id == MAIN_ADMIN_ID:
        return True
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return (await cur.fetchone()) is not None


async def add_admin(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
        await db.commit()


async def remove_admin(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()


async def ensure_user(user_id, username, referred_by=None):
    """Returns True if this call created a new user row."""
    existing = await get_user(user_id)
    if existing:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
            await db.commit()
        return False
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, username, joined_at, trial_used, sub_until, referred_by) "
            "VALUES (?, ?, ?, 0, NULL, ?)",
            (user_id, username, now_iso(), referred_by),
        )
        await db.commit()
    return True


async def get_user(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return await cur.fetchone()


async def start_trial(user_id):
    until = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET trial_used = 1, sub_until = ? WHERE user_id = ?", (until, user_id)
        )
        await db.commit()


async def extend_subscription(user_id, days):
    user = await get_user(user_id)
    now = datetime.datetime.utcnow()
    base = now
    if user and user["sub_until"]:
        try:
            current_until = datetime.datetime.fromisoformat(user["sub_until"])
            if current_until > now:
                base = current_until
        except ValueError:
            pass
    new_until = (base + datetime.timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET sub_until = ?, reminder_sent = 0 WHERE user_id = ?",
            (new_until, user_id),
        )
        await db.commit()
    return new_until


async def revoke_subscription(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET sub_until = NULL WHERE user_id = ?", (user_id,))
        await db.commit()


async def has_access(user_id):
    if not await is_subscription_enabled():
        return True
    user = await get_user(user_id)
    if not user or not user["sub_until"]:
        return False
    try:
        until = datetime.datetime.fromisoformat(user["sub_until"])
    except ValueError:
        return False
    return until > datetime.datetime.utcnow()


async def count_users():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        return (await cur.fetchone())[0]


async def list_users(offset, limit):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        return await cur.fetchall()


async def all_user_ids():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        return [r[0] for r in await cur.fetchall()]


async def users_needing_reminder(hours_ahead=24.0):
    now = datetime.datetime.utcnow()
    hi = (now + datetime.timedelta(hours=hours_ahead)).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM users WHERE sub_until IS NOT NULL AND sub_until BETWEEN ? AND ? "
            "AND reminder_sent = 0",
            (now.isoformat(), hi),
        )
        return await cur.fetchall()


async def mark_reminder_sent(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET reminder_sent = 1 WHERE user_id = ?", (user_id,))
        await db.commit()


async def add_entry(table, name, content):
    key = name.strip().lower()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            f"INSERT INTO {table} (search_key, name, content) VALUES (?, ?, ?) "
            "ON CONFLICT(search_key) DO UPDATE SET name = excluded.name, content = excluded.content",
            (key, name.strip(), content),
        )
        await db.commit()


async def delete_entry(table, name):
    key = name.strip().lower()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(f"DELETE FROM {table} WHERE search_key = ?", (key,))
        await db.commit()


async def find_entry(table, name):
    key = name.strip().lower()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {table} WHERE search_key = ?", (key,))
        return await cur.fetchone()


async def entry_exists(table, name):
    return (await find_entry(table, name)) is not None


async def log_query(query_type, name):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO query_log (query_type, name, created_at) VALUES (?, ?, ?)",
            (query_type, name, now_iso()),
        )
        await db.commit()


async def top_queries(query_type, since_days=7, limit=10):
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=since_days)).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT name, COUNT(*) as cnt FROM query_log "
            "WHERE query_type = ? AND created_at >= ? "
            "GROUP BY LOWER(name) ORDER BY cnt DESC LIMIT ?",
            (query_type, since, limit),
        )
        return await cur.fetchall()


async def set_pending_input(admin_id, action):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO pending_inputs (admin_id, action) VALUES (?, ?) "
            "ON CONFLICT(admin_id) DO UPDATE SET action = excluded.action",
            (admin_id, action),
        )
        await db.commit()


async def get_pending_input(admin_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT action FROM pending_inputs WHERE admin_id = ?", (admin_id,))
        row = await cur.fetchone()
        return row[0] if row else None


async def clear_pending_input(admin_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM pending_inputs WHERE admin_id = ?", (admin_id,))
        await db.commit()


async def set_pending_delete(admin_id, kind, search_key):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO pending_deletes (admin_id, kind, search_key) VALUES (?, ?, ?) "
            "ON CONFLICT(admin_id) DO UPDATE SET kind = excluded.kind, search_key = excluded.search_key",
            (admin_id, kind, search_key),
        )
        await db.commit()


async def get_pending_delete(admin_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM pending_deletes WHERE admin_id = ?", (admin_id,))
        return await cur.fetchone()


async def clear_pending_delete(admin_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM pending_deletes WHERE admin_id = ?", (admin_id,))
        await db.commit()


# ---------- рефералы ----------

async def count_referrals(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
        return (await cur.fetchone())[0]


async def referral_leaderboard(offset, limit):
    """Пользователи, у которых есть хотя бы 1 реферал, отсортированные по убыванию."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT referred_by, COUNT(*) as cnt FROM users "
            "WHERE referred_by IS NOT NULL "
            "GROUP BY referred_by ORDER BY cnt DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return await cur.fetchall()


async def referral_leaderboard_total():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(DISTINCT referred_by) FROM users WHERE referred_by IS NOT NULL"
        )
        return (await cur.fetchone())[0]


# ---------- состояние чата (для автоочистки) ----------

async def get_chat_state(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM chat_state WHERE user_id = ?", (user_id,))
        return await cur.fetchone()


async def set_welcome_msg_id(user_id, msg_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO chat_state (user_id, welcome_msg_id, last_user_msg_id, last_bot_msg_ids) "
            "VALUES (?, ?, NULL, NULL) "
            "ON CONFLICT(user_id) DO UPDATE SET welcome_msg_id = excluded.welcome_msg_id, "
            "last_user_msg_id = NULL, last_bot_msg_ids = NULL",
            (user_id, msg_id),
        )
        await db.commit()


async def save_turn_state(user_id, last_user_msg_id, last_bot_msg_ids):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO chat_state (user_id, welcome_msg_id, last_user_msg_id, last_bot_msg_ids) "
            "VALUES (?, NULL, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_user_msg_id = excluded.last_user_msg_id, "
            "last_bot_msg_ids = excluded.last_bot_msg_ids",
            (user_id, last_user_msg_id, json.dumps(last_bot_msg_ids)),
        )
        await db.commit()


# ============================== KEYBOARDS ==============================

def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 Найти пики / билд", callback_data="menu:search")
    kb.button(text="💎 Моя подписка", callback_data="menu:subscription")
    kb.button(text="👥 Мои рефералы", callback_data="menu:referrals")
    kb.button(text="ℹ️ Как пользоваться", callback_data="menu:help")
    kb.adjust(2, 2)
    return kb.as_markup()


def buy_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Купить", callback_data="buy:subscription")
    return kb.as_markup()


def confirm_kb(action):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да", callback_data=f"confirm:{action}:yes")
    kb.button(text="❌ Отмена", callback_data=f"confirm:{action}:no")
    kb.adjust(2)
    return kb.as_markup()


def users_pagination_kb(prefix, offset, limit, total):
    kb = InlineKeyboardBuilder()
    buttons = []
    if offset > 0:
        buttons.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{prefix}:{max(0, offset - limit)}")
        )
    if offset + limit < total:
        buttons.append(
            InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"{prefix}:{offset + limit}")
        )
    if buttons:
        kb.row(*buttons)
    return kb.as_markup()


# ============================== BOT / DISPATCHER ==============================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

USERS_PAGE_SIZE = 20
BOT_USERNAME = ""  # заполняется при старте

_pending_broadcast_content: dict[int, Message] = {}

# ---------- трекинг отправленных сообщений для автоочистки чата ----------

_track_ctx: ContextVar[list | None] = ContextVar("track_ctx", default=None)

_orig_send_message = Bot.send_message


async def _tracked_send_message(self, *args, **kwargs):
    msg = await _orig_send_message(self, *args, **kwargs)
    lst = _track_ctx.get()
    if lst is not None:
        lst.append(msg.message_id)
    return msg


Bot.send_message = _tracked_send_message


async def cleanup_previous(user_id):
    state = await get_chat_state(user_id)
    if not state:
        return
    welcome_id = state["welcome_msg_id"]
    to_delete = []
    if state["last_user_msg_id"] and state["last_user_msg_id"] != welcome_id:
        to_delete.append(state["last_user_msg_id"])
    if state["last_bot_msg_ids"]:
        try:
            ids = json.loads(state["last_bot_msg_ids"])
        except (TypeError, ValueError):
            ids = []
        for mid in ids:
            if mid != welcome_id:
                to_delete.append(mid)
    for mid in to_delete:
        try:
            await bot.delete_message(user_id, mid)
        except (TelegramForbiddenError, TelegramBadRequest):
            pass


class CleanupMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        is_message = isinstance(event, Message)
        user_id = event.from_user.id if event.from_user else None
        is_start = is_message and bool(event.text and event.text.startswith("/start"))
        is_payment_flow = is_message and bool(event.successful_payment)

        if user_id and not is_start:
            await cleanup_previous(user_id)

        token = _track_ctx.set([])
        try:
            result = await handler(event, data)
        finally:
            sent_ids = _track_ctx.get() or []
            _track_ctx.reset(token)

        if user_id and not is_start and not is_payment_flow:
            incoming_id = event.message_id if is_message else None
            await save_turn_state(user_id, incoming_id, sent_ids)
        return result


dp.message.outer_middleware(CleanupMiddleware())
dp.callback_query.outer_middleware(CleanupMiddleware())


def _fmt_user_card(user):
    lines = [f"👤 ID: {user['user_id']}"]
    if user["username"]:
        lines.append(f"Юзернейм: @{user['username']}")
    lines.append(f"Регистрация: {user['joined_at'][:10]}")
    lines.append(f"Пробный день использован: {'да' if user['trial_used'] else 'нет'}")
    if user["sub_until"]:
        try:
            until = datetime.datetime.fromisoformat(user["sub_until"])
            status = "активна" if until > datetime.datetime.utcnow() else "истекла"
            lines.append(f"Подписка: {status} до {until.strftime('%Y-%m-%d %H:%M')} UTC")
        except ValueError:
            lines.append("Подписка: —")
    else:
        lines.append("Подписка: нет")
    return "\n".join(lines)


def _users_page_text(users, total):
    lines = [f"Всего пользователей: {total}\n"]
    for u in users:
        sub = "нет"
        if u["sub_until"]:
            try:
                until = datetime.datetime.fromisoformat(u["sub_until"])
                sub = "активна" if until > datetime.datetime.utcnow() else "истекла"
            except ValueError:
                pass
        lines.append(f"{u['user_id']} | {u['joined_at'][:10]} | подписка: {sub}")
    return "\n".join(lines)


def _split_first_line(text):
    parts = text.split("\n", 1)
    name = parts[0].strip()
    content = parts[1].strip() if len(parts) > 1 else ""
    return name, content


async def _is_admin_filter(message: Message):
    return await is_admin(message.from_user.id)


# ---------- /start и главное меню ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    parts = message.text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else None

    referred_by = None
    if payload and payload.isdigit():
        candidate = int(payload)
        if candidate != message.from_user.id:
            referred_by = candidate

    await ensure_user(message.from_user.id, message.from_user.username, referred_by=referred_by)

    text = await get_setting("welcome_text")
    sent = await message.answer(text, reply_markup=main_menu_kb())
    await set_welcome_msg_id(message.from_user.id, sent.message_id)


@dp.callback_query(F.data == "menu:search")
async def cb_search(callback: CallbackQuery):
    text = await get_setting("search_prompt_text")
    await callback.message.answer(text)
    await callback.answer()


@dp.callback_query(F.data == "menu:help")
async def cb_help(callback: CallbackQuery):
    text = await get_setting("help_text")
    await callback.message.answer(text)
    await callback.answer()


@dp.callback_query(F.data == "menu:referrals")
async def cb_referrals(callback: CallbackQuery):
    user_id = callback.from_user.id
    count = await count_referrals(user_id)
    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    template = await get_setting("referral_text")
    await callback.message.answer(template.format(link=link, count=count))
    await callback.answer()


# ---------- админ: карты / билды ----------

@dp.message(Command("addmap"), _is_admin_filter)
async def cmd_addmap(message: Message):
    raw = message.text[len("/addmap"):].strip()
    if not raw:
        await message.answer("Пришли одним сообщением:\nНазвание карты\nдальше — пики (любой текст)")
        return
    name, content = _split_first_line(raw)
    if not name or not content:
        await message.answer("Нужно название карты на первой строке и содержимое дальше.")
        return
    await add_entry("maps", name, content)
    await message.answer(f"✅ Карта «{name}» сохранена.")


@dp.message(Command("addbuild"), _is_admin_filter)
async def cmd_addbuild(message: Message):
    raw = message.text[len("/addbuild"):].strip()
    if not raw:
        await message.answer("Пришли одним сообщением:\nНазвание бойца\nдальше — билд (любой текст)")
        return
    name, content = _split_first_line(raw)
    if not name or not content:
        await message.answer("Нужно название бойца на первой строке и содержимое дальше.")
        return
    await add_entry("builds", name, content)
    await message.answer(f"✅ Билд на «{name}» сохранён.")


@dp.message(Command("delmap"), _is_admin_filter)
async def cmd_delmap(message: Message):
    name = message.text[len("/delmap"):].strip()
    if not name:
        await message.answer("Формат: /delmap Название карты")
        return
    if not await entry_exists("maps", name):
        await message.answer(f"Карта «{name}» не найдена.")
        return
    await set_pending_delete(message.from_user.id, "maps", name.lower())
    await message.answer(f"Удалить карту «{name}»?", reply_markup=confirm_kb("delete"))


@dp.message(Command("delbuild"), _is_admin_filter)
async def cmd_delbuild(message: Message):
    name = message.text[len("/delbuild"):].strip()
    if not name:
        await message.answer("Формат: /delbuild Название бойца")
        return
    if not await entry_exists("builds", name):
        await message.answer(f"Билд на «{name}» не найден.")
        return
    await set_pending_delete(message.from_user.id, "builds", name.lower())
    await message.answer(f"Удалить билд на «{name}»?", reply_markup=confirm_kb("delete"))


@dp.callback_query(F.data.startswith("confirm:delete:"))
async def cb_confirm_delete(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    answer = callback.data.split(":")[-1]
    pending = await get_pending_delete(callback.from_user.id)
    if not pending:
        await callback.message.edit_text("Нечего удалять — запрос устарел.")
        await callback.answer()
        return
    await clear_pending_delete(callback.from_user.id)
    if answer == "yes":
        await delete_entry(pending["kind"], pending["search_key"])
        await callback.message.edit_text("✅ Удалено")
    else:
        await callback.message.edit_text("Отменено")
    await callback.answer()


# ---------- админ: пользователи, подписки, админы, рефералы ----------

@dp.message(Command("addsub"), _is_admin_filter)
async def cmd_addsub(message: Message):
    args = message.text.split()[1:]
    if len(args) != 2 or not args[0].isdigit() or not args[1].lstrip("-").isdigit():
        await message.answer("Формат: /addsub <user_id> <дней>")
        return
    user_id, days = int(args[0]), int(args[1])
    user = await get_user(user_id)
    if not user:
        await message.answer("Пользователь с таким id не найден в базе (он ещё не писал боту).")
        return
    new_until = await extend_subscription(user_id, days)
    await message.answer(f"✅ Подписка для {user_id} продлена до {new_until[:16]} UTC")


@dp.message(Command("delsub"), _is_admin_filter)
async def cmd_delsub(message: Message):
    args = message.text.split()[1:]
    if len(args) != 1 or not args[0].isdigit():
        await message.answer("Формат: /delsub <user_id>")
        return
    await revoke_subscription(int(args[0]))
    await message.answer(f"✅ Подписка для {args[0]} снята")


@dp.message(Command("addadmin"), _is_admin_filter)
async def cmd_addadmin(message: Message):
    args = message.text.split()[1:]
    if len(args) != 1 or not args[0].isdigit():
        await message.answer("Формат: /addadmin <user_id>")
        return
    await add_admin(int(args[0]))
    await message.answer(f"✅ {args[0]} теперь админ")


@dp.message(Command("deladmin"), _is_admin_filter)
async def cmd_deladmin(message: Message):
    args = message.text.split()[1:]
    if len(args) != 1 or not args[0].isdigit():
        await message.answer("Формат: /deladmin <user_id>")
        return
    await remove_admin(int(args[0]))
    await message.answer(f"✅ {args[0]} больше не админ")


@dp.message(Command("users"), _is_admin_filter)
async def cmd_users(message: Message):
    args = message.text.split()[1:]
    if args and args[0].isdigit():
        user = await get_user(int(args[0]))
        if not user:
            await message.answer("Пользователь с таким id не найден.")
            return
        await message.answer(_fmt_user_card(user))
        return
    total = await count_users()
    users = await list_users(0, USERS_PAGE_SIZE)
    if not users:
        await message.answer("Пока нет пользователей.")
        return
    await message.answer(
        _users_page_text(users, total),
        reply_markup=users_pagination_kb("users_page", 0, USERS_PAGE_SIZE, total),
    )


@dp.callback_query(F.data.startswith("users_page:"))
async def cb_users_page(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    offset = int(callback.data.split(":")[1])
    total = await count_users()
    users = await list_users(offset, USERS_PAGE_SIZE)
    await callback.message.edit_text(
        _users_page_text(users, total),
        reply_markup=users_pagination_kb("users_page", offset, USERS_PAGE_SIZE, total),
    )
    await callback.answer()


@dp.message(Command("referrals"), _is_admin_filter)
async def cmd_referrals(message: Message):
    total = await referral_leaderboard_total()
    rows = await referral_leaderboard(0, USERS_PAGE_SIZE)
    if not rows:
        await message.answer("Пока никто никого не пригласил.")
        return
    lines = [f"👥 Всего пригласивших: {total}\n"]
    for user_id, cnt in rows:
        lines.append(f"{user_id} — {cnt} чел.")
    await message.answer(
        "\n".join(lines),
        reply_markup=users_pagination_kb("ref_page", 0, USERS_PAGE_SIZE, total),
    )


@dp.callback_query(F.data.startswith("ref_page:"))
async def cb_ref_page(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    offset = int(callback.data.split(":")[1])
    total = await referral_leaderboard_total()
    rows = await referral_leaderboard(offset, USERS_PAGE_SIZE)
    lines = [f"👥 Всего пригласивших: {total}\n"]
    for user_id, cnt in rows:
        lines.append(f"{user_id} — {cnt} чел.")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=users_pagination_kb("ref_page", offset, USERS_PAGE_SIZE, total),
    )
    await callback.answer()


# ---------- админ: рассылка ----------

@dp.message(Command("broadcast"), _is_admin_filter)
async def cmd_broadcast(message: Message):
    await set_pending_input(message.from_user.id, "broadcast")
    await message.answer("Пришли сообщение для рассылки (текст, фото — что угодно).")


async def _awaiting_broadcast(message: Message):
    if not await is_admin(message.from_user.id):
        return False
    return await get_pending_input(message.from_user.id) == "broadcast"


@dp.message(_awaiting_broadcast)
async def receive_broadcast_content(message: Message):
    await clear_pending_input(message.from_user.id)
    _pending_broadcast_content[message.from_user.id] = message
    total = await count_users()
    await message.answer(
        f"Разослать это сообщение всем пользователям ({total} чел.)?",
        reply_markup=confirm_kb("broadcast"),
    )


@dp.callback_query(F.data.startswith("confirm:broadcast:"))
async def cb_confirm_broadcast(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    answer = callback.data.split(":")[-1]
    source = _pending_broadcast_content.pop(callback.from_user.id, None)
    if answer != "yes" or source is None:
        await callback.message.edit_text("Отменено")
        await callback.answer()
        return
    await callback.message.edit_text("⏳ Рассылаю...")
    user_ids = await all_user_ids()
    sent, failed = 0, 0
    for i, user_id in enumerate(user_ids, 1):
        try:
            await source.copy_to(chat_id=user_id)
            sent += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        await asyncio.sleep(0.05)
        if i % 50 == 0:
            await callback.message.answer(f"Прогресс: {i}/{len(user_ids)}")
    await callback.message.answer(f"✅ Рассылка завершена. Отправлено: {sent}. Не доставлено: {failed}.")
    await callback.answer()


# ---------- админ: статистика ----------

@dp.message(Command("stats"), _is_admin_filter)
async def cmd_stats(message: Message):
    top_maps = await top_queries("map", since_days=7)
    top_builds = await top_queries("build", since_days=7)
    lines = ["📊 Топ запросов за неделю\n", "🗺 Карты:"]
    if top_maps:
        for i, (name, cnt) in enumerate(top_maps, 1):
            lines.append(f"{i}. {name} — {cnt}")
    else:
        lines.append("нет данных")
    lines.append("\n⚔️ Бойцы:")
    if top_builds:
        for i, (name, cnt) in enumerate(top_builds, 1):
            lines.append(f"{i}. {name} — {cnt}")
    else:
        lines.append("нет данных")
    await message.answer("\n".join(lines))


# ---------- админ: режим подписки ----------

@dp.message(Command("subscription"), _is_admin_filter)
async def cmd_subscription(message: Message):
    args = message.text.split()[1:]
    if not args or args[0] not in ("on", "off"):
        current = await is_subscription_enabled()
        await message.answer(
            f"Текущий режим: {'платный' if current else 'бесплатный (бета)'}.\nФормат: /subscription on|off"
        )
        return
    await set_setting("subscription_enabled", "1" if args[0] == "on" else "0")
    if args[0] == "on":
        await message.answer(
            "✅ Платный режим включён. Пробный день выдаётся только тем, кто им ещё не пользовался."
        )
    else:
        await message.answer("✅ Платный режим выключен — доступ у всех бесплатный.")


# ---------- админ: редактируемые тексты (одна команда на каждый текст) ----------

def _make_settext_handlers(key: str, command_name: str, prompt: str):
    async def cmd_handler(message: Message):
        await set_pending_input(message.from_user.id, f"settext:{key}")
        await message.answer(prompt)

    async def awaiting_filter(message: Message):
        if not await is_admin(message.from_user.id):
            return False
        return await get_pending_input(message.from_user.id) == f"settext:{key}"

    async def receive_handler(message: Message):
        await clear_pending_input(message.from_user.id)
        if not message.text:
            await message.answer(f"Нужен текст. Попробуй снова: /{command_name}")
            return
        await set_setting(key, message.text)
        await message.answer("✅ Текст обновлён")

    dp.message.register(cmd_handler, Command(command_name), _is_admin_filter)
    dp.message.register(receive_handler, awaiting_filter)


for _key, (_cmd, _prompt, _default) in EDITABLE_TEXTS.items():
    _make_settext_handlers(_key, _cmd, _prompt)


# ---------- подписка / оплата (Telegram Stars) ----------

INVOICE_PAYLOAD = "subscription_30d"


@dp.callback_query(F.data == "menu:subscription")
async def cb_subscription_status(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if user and user["sub_until"]:
        try:
            until = datetime.datetime.fromisoformat(user["sub_until"])
        except ValueError:
            until = None
        if until and until > datetime.datetime.utcnow():
            left = until - datetime.datetime.utcnow()
            template = await get_setting("sub_active_text")
            await callback.message.answer(
                template.format(days=left.days, hours=left.seconds // 3600)
            )
            await callback.answer()
            return
    text = await get_setting("no_access_text")
    await callback.message.answer(text, reply_markup=buy_kb())
    await callback.answer()


@dp.callback_query(F.data == "buy:subscription")
async def cb_buy(callback: CallbackQuery):
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Подписка на бота",
        description=f"{SUBSCRIPTION_DAYS} дней доступа к пикам и билдам",
        payload=INVOICE_PAYLOAD,
        currency="XTR",
        prices=[LabeledPrice(label="Подписка", amount=SUBSCRIPTION_STARS)],
        provider_token="",
    )
    await callback.answer()


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    await extend_subscription(message.from_user.id, SUBSCRIPTION_DAYS)
    template = await get_setting("payment_success_text")
    await message.answer(template.format(days=SUBSCRIPTION_DAYS))


# ---------- поиск (общий обработчик текста, регистрируем последним) ----------

async def _not_pending_admin_input(message: Message):
    if not await is_admin(message.from_user.id):
        return True
    pending = await get_pending_input(message.from_user.id)
    pending_del = await get_pending_delete(message.from_user.id)
    return pending is None and pending_del is None


@dp.message(F.text, _not_pending_admin_input)
async def handle_search(message: Message):
    query = message.text.strip()
    if not query or query.startswith("/"):
        return

    await ensure_user(message.from_user.id, message.from_user.username)
    user_id = message.from_user.id
    user = await get_user(user_id)

    if await is_subscription_enabled() and user and not user["trial_used"]:
        await start_trial(user_id)

    if not await has_access(user_id):
        text = await get_setting("no_access_text")
        await message.answer(text, reply_markup=buy_kb())
        return

    map_entry = await find_entry("maps", query)
    build_entry = await find_entry("builds", query)

    if not map_entry and not build_entry:
        text = await get_setting("not_found_text")
        await message.answer(text)
        return

    if map_entry:
        await log_query("map", map_entry["name"])
        await message.answer(map_entry["content"])
    if build_entry:
        await log_query("build", build_entry["name"])
        await message.answer(build_entry["content"])


# ============================== ФОНОВЫЕ ЗАДАЧИ / ЗАПУСК ==============================

async def reminder_loop():
    while True:
        try:
            users = await users_needing_reminder(hours_ahead=24)
            template = await get_setting("reminder_text")
            for user in users:
                try:
                    await bot.send_message(user["user_id"], template)
                except (TelegramForbiddenError, TelegramBadRequest):
                    pass
                await mark_reminder_sent(user["user_id"])
        except Exception:
            logging.exception("reminder_loop error")
        await asyncio.sleep(3600)


async def main():
    global BOT_USERNAME
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан (переменная окружения)")
    await init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username
    asyncio.create_task(reminder_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
