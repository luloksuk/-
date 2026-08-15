import asyncio
import datetime
import json
import logging
import os
import re
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

SUBSCRIPTION_TARIFFS = {
    "15": {"days": 15, "stars": 70, "label": "15 дней — 70⭐"},
    "30": {"days": 30, "stars": 100, "label": "30 дней — 100⭐"},
}

# ---- редактируемые тексты (ключ -> (админ-команда, подсказка админу, дефолт)) ----

DEFAULT_WELCOME_TEXT = (
    "👋 Привет, {name}! Напиши название карты — пришлю пики, "
    "или название бойца — пришлю билд."
)
DEFAULT_HELP_TEXT = (
    "ℹ️ Как пользоваться\n"
    "Напиши название карты — пришлю пики (лучших бойцов на неё).\n"
    "Напиши название бойца — пришлю билд.\n"
    "Например: «Гейзер» или «Шелли».\n\n"
    "💎 Подписка\n"
    "1 день бесплатно при первом запросе, дальше — от 70⭐ за 15 дней.\n\n"
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
DEFAULT_PURCHASE_CONFIRM_TEXT = "Вы хотите купить подписку на {days} дней за {stars}⭐?"
DEFAULT_DRAFT_INTRO_TEXT = (
    "🧩 Режим драфта включён.\n"
    "Напиши название карты, а со второй строки — всех 6 забаненных бойцов "
    "(через запятую или каждого с новой строки)."
)
DEFAULT_DRAFT_FP_HEADER_TEXT = "🥇 Рекомендованные ФП (с учётом банов):"
DEFAULT_DRAFT_PICK_PROMPT_TEXT = "Теперь пиши вражеских бойцов по одному, как только они пикают."

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
    "purchase_confirm_text": (
        "setpurchaseconfirm",
        "Пришли новый текст подтверждения перед покупкой. Можно использовать {days} и {stars}.",
        DEFAULT_PURCHASE_CONFIRM_TEXT,
    ),
    "draft_intro_text": (
        "setdraftintro",
        "Пришли новый текст приветствия режима драфта (когда его включают).",
        DEFAULT_DRAFT_INTRO_TEXT,
    ),
    "draft_fp_header_text": (
        "setdraftfpheader",
        "Пришли новый заголовок для списка рекомендованных ФП-бойцов.",
        DEFAULT_DRAFT_FP_HEADER_TEXT,
    ),
    "draft_pick_prompt_text": (
        "setdraftpickprompt",
        "Пришли новый текст приглашения вводить вражеских бойцов.",
        DEFAULT_DRAFT_PICK_PROMPT_TEXT,
    ),
}

# ---- редактируемые названия кнопок главного меню ----

BUTTON_LABELS = {
    "btn_subscription": ("setbtnsub", "Пришли новое название кнопки подписки.", "💎 Моя подписка"),
    "btn_referrals": ("setbtnref", "Пришли новое название кнопки рефералов.", "👥 Мои рефералы"),
    "btn_help": ("setbtnhelp", 'Пришли новое название кнопки "Как пользоваться".', "ℹ️ Как пользоваться"),
    "btn_promo": ("setbtnpromo", 'Пришли новое название кнопки "Ввести промокод".', "🎟 Ввести промокод"),
    "btn_draft": ("setbtndraft", 'Пришли новое название кнопки "Драфт".', "🧩 Драфт"),
    "btn_draft_exit": ("setbtndraftexit", 'Пришли новое название кнопки "Выйти из драфта".', "❌ Выйти из драфта"),
}

ADMIN_COMMANDS_HELP = """
🗺 <b>Карты и билды</b>
/addmap — добавить или обновить карту. Первая строка сообщения — название карты (по нему её ищут пользователи), всё что дальше — содержимое (пики); можно с форматированием, платными эмодзи и фото. После публикации бот попросит выбрать тип карты (Открытая/Средняя/Закрытая) — это влияет на рекомендации в режиме "Драфт".
/delmap Название — удалить карту. Бот покажет её содержимое и попросит подтвердить.
/addbuild — то же самое, но для билда бойца (первая строка — имя бойца).
/delbuild Имя — удалить билд бойца, с подтверждением.
/listmaps — список всех карт, которые сейчас есть в базе.
/listbuilds — список всех билдов, которые сейчас есть в базе.

🧩 <b>Драфт-помощник</b>
/addcounter — задать, кого контрит конкретный боец. Имя на первой строке, дальше через запятую — список тех, кого он контрит.
/delcounter Имя — удалить список контр для этого бойца.
/listcounters — посмотреть все заданные контры разом.
/setmaptypeheroes — задать список бойцов, сильных на конкретном типе карты. Бот спросит тип кнопками, затем — список бойцов через запятую. Используется и как "рекомендованные ФП" в начале драфта, и как бонус к очкам при подборе контрпика.
Кнопка "🧩 Драфт" в меню запускает сессию: пользователь присылает карту и баны одним сообщением (карта — первая строка, баны — со второй), бот показывает сохранённые пики по карте и рекомендованных ФП с учётом банов, дальше пользователь присылает вражеских бойцов по одному — бот считает лучший контрпик.

👤 <b>Пользователи и подписки</b>
/addsub id дней — выдать/продлить подписку вручную, минуя оплату.
/delsub id — снять подписку у пользователя.
/addsuball дней — разово выдать указанное число дней подписки вообще всем пользователям сразу, с уведомлением каждому.
/users — список пользователей с пагинацией (или /users id — карточка конкретного).
/referrals — список тех, кто кого-то пригласил: сколько привёл и сколько из них купили подписку (или /referrals id — по конкретному человеку). За каждые 3 приглашённых рефереру автоматически начисляется 1 день подписки, с уведомлением.

🎟 <b>Промокоды</b>
/addpromo — создать промокод: код, тип (percent — скидка % на покупку, или days — дней подписки бесплатно), значение, лимит использований, период "остывания" в днях.
/delpromo КОД — удалить промокод.
/promos — список всех промокодов и статистика использования.
Пользователи вводят код через кнопку "🎟 Ввести промокод" рядом с тарифами.

📢 <b>Рассылка и статистика</b>
/broadcast — разослать любое сообщение всем пользователям (с подтверждением перед отправкой).
/stats — топ самых частых запросов (карты и бойцы отдельно) за неделю.
/export — CSV-файл со всеми пользователями.
/growth — картинка-график роста числа пользователей.

⚙️ <b>Настройки бота</b>
/subscription on|off — вкл/выкл платный режим (off = бесплатно всем, для беты; пробный день не выдаётся повторно тем, кто уже им пользовался).
/maintenance on|off — режим техработ: пока включён, время подписки не расходуется; при выключении бот сам продлевает всем на то время, что шли работы.
/addadmin id, /deladmin id — управление админами (у админов всегда безлимитный доступ).

✏️ <b>Редактируемые тексты</b> (можно прислать как фото с подписью)
/setwelcome — приветствие при /start (можно {name})
/sethelp — раздел "Как пользоваться"
/setnoaccess — сообщение об отсутствии подписки
/setsearchprompt — подсказка кнопки поиска
/setnotfound — сообщение "не найдено"
/setsubactive — статус активной подписки ({days}, {hours})
/setreminder — напоминание об истечении подписки
/setpaymentsuccess — сообщение после оплаты ({days})
/setreferral — раздел "Мои рефералы" ({link}, {count})
/setpurchaseconfirm — подтверждение перед покупкой ({days}, {stars})
/setdraftintro — приветствие режима драфта
/setdraftfpheader — заголовок списка рекомендованных ФП
/setdraftpickprompt — приглашение вводить вражеских бойцов

🔘 <b>Названия кнопок меню</b> (пришли платный эмодзи вместе с текстом — станет иконкой)
/setbtnsub — "Моя подписка"
/setbtnref — "Мои рефералы"
/setbtnhelp — "Как пользоваться"
/setbtnpromo — "Ввести промокод"
/setbtndraft — "Драфт" в главном меню
/setbtndraftexit — "Выйти из драфта" внутри сессии

ℹ️ /admin — показать этот список
""".strip()

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
    content TEXT NOT NULL,
    photo_file_id TEXT
);
CREATE TABLE IF NOT EXISTS builds (
    search_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    photo_file_id TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS setting_photos (
    key TEXT PRIMARY KEY,
    photo_file_id TEXT
);
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_type TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_inputs (
    admin_id INTEGER PRIMARY KEY,
    action TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_actions (
    admin_id INTEGER PRIMARY KEY,
    action_type TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_state (
    user_id INTEGER PRIMARY KEY,
    welcome_msg_id INTEGER,
    last_user_msg_id INTEGER,
    last_bot_msg_ids TEXT
);
CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    value INTEGER NOT NULL,
    uses_limit INTEGER NOT NULL,
    uses_count INTEGER NOT NULL DEFAULT 0,
    cooldown_days INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (
    search_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    counters TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS map_type_heroes (
    map_type TEXT PRIMARY KEY,
    heroes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS draft_sessions (
    user_id INTEGER PRIMARY KEY,
    stage TEXT NOT NULL,
    map_type TEXT,
    banned TEXT,
    enemies TEXT
);
"""


async def _ensure_column(db, table, column, coltype):
    cur = await db.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in await cur.fetchall()]
    if column not in cols:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(SCHEMA)
        # Миграции для баз, созданных более старой версией кода
        # (CREATE TABLE IF NOT EXISTS не добавляет новые столбцы в уже существующие таблицы).
        await _ensure_column(db, "users", "referred_by", "INTEGER")
        await _ensure_column(db, "users", "reminder_sent", "INTEGER NOT NULL DEFAULT 0")
        await _ensure_column(db, "users", "has_purchased", "INTEGER NOT NULL DEFAULT 0")
        await _ensure_column(db, "users", "referral_rewards_given", "INTEGER NOT NULL DEFAULT 0")
        await _ensure_column(db, "users", "pending_discount_percent", "INTEGER")
        await _ensure_column(db, "users", "promo_cooldown_until", "TEXT")
        await _ensure_column(db, "maps", "photo_file_id", "TEXT")
        await _ensure_column(db, "maps", "map_type", "TEXT")
        await _ensure_column(db, "builds", "photo_file_id", "TEXT")
        await db.commit()
        for key, (_, _, default) in EDITABLE_TEXTS.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, default)
            )
        for key, (_, _, default) in BUTTON_LABELS.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, default)
            )
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("subscription_enabled", "1"),
        )
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("maintenance_mode", "0"),
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


async def get_setting_photo(key):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT photo_file_id FROM setting_photos WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


async def set_setting_photo(key, photo_file_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        if photo_file_id:
            await db.execute(
                "INSERT INTO setting_photos (key, photo_file_id) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET photo_file_id = excluded.photo_file_id",
                (key, photo_file_id),
            )
        else:
            await db.execute("DELETE FROM setting_photos WHERE key = ?", (key,))
        await db.commit()


async def is_subscription_enabled():
    return await get_setting("subscription_enabled") == "1"


async def is_maintenance_mode():
    return await get_setting("maintenance_mode") == "1"


async def start_maintenance():
    await set_setting("maintenance_mode", "1")
    await set_setting("maintenance_started_at", now_iso())


async def end_maintenance():
    """Выключает режим техработ и сдвигает подписки всех пользователей
    вперёд ровно на то время, что шли техработы (чтобы это время не
    списывалось со срока подписки)."""
    started_raw = await get_setting("maintenance_started_at")
    await set_setting("maintenance_mode", "0")
    if not started_raw:
        return 0
    try:
        started = datetime.datetime.fromisoformat(started_raw)
    except ValueError:
        return 0
    elapsed = datetime.datetime.utcnow() - started
    if elapsed.total_seconds() <= 0:
        return 0
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT user_id, sub_until FROM users WHERE sub_until IS NOT NULL")
        rows = await cur.fetchall()
        for row in rows:
            try:
                until = datetime.datetime.fromisoformat(row["sub_until"])
            except ValueError:
                continue
            new_until = (until + elapsed).isoformat()
            await db.execute(
                "UPDATE users SET sub_until = ? WHERE user_id = ?", (new_until, row["user_id"])
            )
        await db.commit()
    return int(elapsed.total_seconds())


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


async def get_all_admin_ids():
    ids = set()
    if MAIN_ADMIN_ID:
        ids.add(MAIN_ADMIN_ID)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT user_id FROM admins")
        for row in await cur.fetchall():
            ids.add(row[0])
    return list(ids)


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
    if await is_admin(user_id):
        return True
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


async def add_entry(table, name, content, photo_file_id=None):
    key = name.strip().lower()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            f"INSERT INTO {table} (search_key, name, content, photo_file_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(search_key) DO UPDATE SET name = excluded.name, content = excluded.content, "
            "photo_file_id = excluded.photo_file_id",
            (key, name.strip(), content, photo_file_id),
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


async def list_entry_names(table):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(f"SELECT name FROM {table} ORDER BY name COLLATE NOCASE")
        return [r[0] for r in await cur.fetchall()]


async def set_map_type(search_key, map_type):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE maps SET map_type = ? WHERE search_key = ?", (map_type, search_key))
        await db.commit()


# ---------- контры бойцов (для драфт-помощника) ----------

MAP_TYPES = ["Открытая", "Средняя", "Закрытая"]


async def set_counters(hero_name, counters_list):
    key = hero_name.strip().lower()
    joined = ", ".join(c.strip() for c in counters_list if c.strip())
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO counters (search_key, name, counters) VALUES (?, ?, ?) "
            "ON CONFLICT(search_key) DO UPDATE SET name = excluded.name, counters = excluded.counters",
            (key, hero_name.strip(), joined),
        )
        await db.commit()


async def get_counters(hero_name):
    key = hero_name.strip().lower()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT counters FROM counters WHERE search_key = ?", (key,))
        row = await cur.fetchone()
        if not row or not row[0]:
            return []
        return [c.strip() for c in row[0].split(",") if c.strip()]


async def delete_counters(hero_name):
    key = hero_name.strip().lower()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM counters WHERE search_key = ?", (key,))
        await db.commit()


async def counters_exists(hero_name):
    return bool(await get_counters(hero_name)) or await _counters_row_exists(hero_name)


async def _counters_row_exists(hero_name):
    key = hero_name.strip().lower()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT 1 FROM counters WHERE search_key = ?", (key,))
        return (await cur.fetchone()) is not None


async def list_all_counters():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM counters ORDER BY name COLLATE NOCASE")
        return await cur.fetchall()


async def set_map_type_heroes(map_type, heroes_list):
    joined = ", ".join(h.strip() for h in heroes_list if h.strip())
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO map_type_heroes (map_type, heroes) VALUES (?, ?) "
            "ON CONFLICT(map_type) DO UPDATE SET heroes = excluded.heroes",
            (map_type, joined),
        )
        await db.commit()


async def get_map_type_heroes(map_type):
    if not map_type:
        return []
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT heroes FROM map_type_heroes WHERE map_type = ?", (map_type,))
        row = await cur.fetchone()
        if not row or not row[0]:
            return []
        return [h.strip() for h in row[0].split(",") if h.strip()]


# ---------- сессии драфта ----------

async def get_draft_session(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM draft_sessions WHERE user_id = ?", (user_id,))
        return await cur.fetchone()


async def start_draft_session(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO draft_sessions (user_id, stage, map_type, banned, enemies) "
            "VALUES (?, 'await_map_bans', NULL, '[]', '[]') "
            "ON CONFLICT(user_id) DO UPDATE SET stage = 'await_map_bans', map_type = NULL, "
            "banned = '[]', enemies = '[]'",
            (user_id,),
        )
        await db.commit()


async def end_draft_session(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM draft_sessions WHERE user_id = ?", (user_id,))
        await db.commit()


async def update_draft_session(user_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(f"UPDATE draft_sessions SET {cols} WHERE user_id = ?", values)
        await db.commit()


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


async def set_pending_action(admin_id, action_type, payload: dict):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO pending_actions (admin_id, action_type, payload) VALUES (?, ?, ?) "
            "ON CONFLICT(admin_id) DO UPDATE SET action_type = excluded.action_type, "
            "payload = excluded.payload",
            (admin_id, action_type, json.dumps(payload)),
        )
        await db.commit()


async def get_pending_action(admin_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM pending_actions WHERE admin_id = ?", (admin_id,))
        row = await cur.fetchone()
        if not row:
            return None
        return {"action_type": row["action_type"], "payload": json.loads(row["payload"])}


async def clear_pending_action(admin_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM pending_actions WHERE admin_id = ?", (admin_id,))
        await db.commit()


# ---------- промокоды ----------

async def create_promo(code, type_, value, uses_limit, cooldown_days):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO promo_codes (code, type, value, uses_limit, uses_count, cooldown_days, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET type = excluded.type, value = excluded.value, "
            "uses_limit = excluded.uses_limit, cooldown_days = excluded.cooldown_days",
            (code.upper(), type_, value, uses_limit, cooldown_days, now_iso()),
        )
        await db.commit()


async def get_promo(code):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM promo_codes WHERE code = ?", (code.upper(),))
        return await cur.fetchone()


async def delete_promo(code):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM promo_codes WHERE code = ?", (code.upper(),))
        await db.commit()


async def list_promos():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC")
        return await cur.fetchall()


async def increment_promo_uses(code):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE promo_codes SET uses_count = uses_count + 1 WHERE code = ?", (code.upper(),)
        )
        await db.commit()


async def set_promo_cooldown(user_id, cooldown_days):
    until = (datetime.datetime.utcnow() + datetime.timedelta(days=cooldown_days)).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET promo_cooldown_until = ? WHERE user_id = ?", (until, user_id)
        )
        await db.commit()


async def is_in_promo_cooldown(user_id):
    user = await get_user(user_id)
    if not user or not user["promo_cooldown_until"]:
        return False
    try:
        until = datetime.datetime.fromisoformat(user["promo_cooldown_until"])
    except ValueError:
        return False
    return until > datetime.datetime.utcnow()


async def set_pending_discount(user_id, percent):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET pending_discount_percent = ? WHERE user_id = ?", (percent, user_id)
        )
        await db.commit()


async def clear_pending_discount(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET pending_discount_percent = NULL WHERE user_id = ?", (user_id,)
        )
        await db.commit()


# ---------- рефералы ----------

async def mark_purchased(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET has_purchased = 1 WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_referral_rewards_given(user_id):
    user = await get_user(user_id)
    return user["referral_rewards_given"] if user else 0


async def set_referral_rewards_given(user_id, value):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET referral_rewards_given = ? WHERE user_id = ?", (value, user_id)
        )
        await db.commit()


async def count_referrals(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
        return (await cur.fetchone())[0]


async def count_referral_purchases(user_id):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by = ? AND has_purchased = 1", (user_id,)
        )
        return (await cur.fetchone())[0]


async def referral_leaderboard(offset, limit):
    """Пользователи, у которых есть хотя бы 1 реферал, отсортированные по убыванию,
    вместе с числом рефералов, которые реально купили подписку."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT referred_by, COUNT(*) as cnt, "
            "SUM(CASE WHEN has_purchased = 1 THEN 1 ELSE 0 END) as purchased_cnt "
            "FROM users "
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

async def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(
        text=await get_setting("btn_draft"),
        callback_data="menu:draft",
        style="success",
        icon_custom_emoji_id=await get_setting("btn_draft_icon") or None,
    )
    kb.button(
        text=await get_setting("btn_subscription"),
        callback_data="menu:subscription",
        style="success",
        icon_custom_emoji_id=await get_setting("btn_subscription_icon") or None,
    )
    kb.button(
        text=await get_setting("btn_referrals"),
        callback_data="menu:referrals",
        style="success",
        icon_custom_emoji_id=await get_setting("btn_referrals_icon") or None,
    )
    kb.button(
        text=await get_setting("btn_help"),
        callback_data="menu:help",
        style="success",
        icon_custom_emoji_id=await get_setting("btn_help_icon") or None,
    )
    kb.adjust(2, 2)
    return kb.as_markup()


async def buy_kb():
    kb = InlineKeyboardBuilder()
    for key, tariff in SUBSCRIPTION_TARIFFS.items():
        kb.button(text=f"💳 {tariff['label']}", callback_data=f"buy:{key}", style="success")
    kb.button(
        text=await get_setting("btn_promo"),
        callback_data="menu:promo",
        style="primary",
        icon_custom_emoji_id=await get_setting("btn_promo_icon") or None,
    )
    kb.adjust(1)
    return kb.as_markup()


def confirm_kb(action):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да", callback_data=f"confirm:{action}:yes", style="success")
    kb.button(text="❌ Отмена", callback_data=f"confirm:{action}:no", style="danger")
    kb.adjust(2)
    return kb.as_markup()


APPLY_HANDLERS = {}


def register_apply(action_type):
    def deco(fn):
        APPLY_HANDLERS[action_type] = fn
        return fn
    return deco


def action_confirm_kb(yes_label="✅ Подтвердить", no_label="❌ Отменить"):
    """Клавиатура для универсального механизма предпросмотра/подтверждения
    админ-действий (pending_actions)."""
    kb = InlineKeyboardBuilder()
    kb.button(text=yes_label, callback_data="actconfirm:yes", style="success")
    kb.button(text=no_label, callback_data="actconfirm:no", style="danger")
    kb.adjust(2)
    return kb.as_markup()


def users_pagination_kb(prefix, offset, limit, total):
    kb = InlineKeyboardBuilder()
    buttons = []
    if offset > 0:
        buttons.append(
            InlineKeyboardButton(
                text="◀️ Назад", callback_data=f"{prefix}:{max(0, offset - limit)}", style="success"
            )
        )
    if offset + limit < total:
        buttons.append(
            InlineKeyboardButton(
                text="Вперёд ▶️", callback_data=f"{prefix}:{offset + limit}", style="success"
            )
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


async def reply(event, text, *, photo_file_id=None, **kwargs):
    """Отправляет сообщение (текстом или фото+подпись) как ответ на Message
    или CallbackQuery, и запоминает id отправленного сообщения для
    последующей автоочистки чата."""
    target = event.message if isinstance(event, CallbackQuery) else event
    if photo_file_id:
        msg = await target.answer_photo(photo_file_id, caption=text, **kwargs)
    else:
        msg = await target.answer(text, **kwargs)
    lst = _track_ctx.get()
    if lst is not None:
        lst.append(msg.message_id)
    return msg


async def send_tracked(chat_id, text, **kwargs):
    """То же, что reply(), но когда нет исходного Message/CallbackQuery —
    просто chat_id (например, для доп. сообщения из apply-хендлера)."""
    msg = await bot.send_message(chat_id, text, **kwargs)
    lst = _track_ctx.get()
    if lst is not None:
        lst.append(msg.message_id)
    return msg


async def get_deletable_ids(user_id):
    """Возвращает id сообщений предыдущего обмена (не приветствия), которые
    нужно удалить после того как отправится новый ответ."""
    state = await get_chat_state(user_id)
    if not state:
        return []
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
    return to_delete


async def delete_messages(user_id, message_ids):
    for mid in message_ids:
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

        to_delete = []
        if user_id and not is_start:
            to_delete = await get_deletable_ids(user_id)

        token = _track_ctx.set([])
        try:
            result = await handler(event, data)
        finally:
            sent_ids = _track_ctx.get() or []
            _track_ctx.reset(token)

        # Новый ответ уже отправлен (sent_ids) — только теперь удаляем старый.
        if to_delete:
            await delete_messages(user_id, to_delete)

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


def _extract_name_and_content_html(message: Message, command: str):
    """Достаёт название (первая строка, как обычный текст — для поиска),
    контент (всё остальное, как HTML — форматирование и платные эмодзи
    сохраняются) и id прикреплённого фото (если оно есть), из сообщения
    вида "/addmap Название\\nКонтент" (текстом или фото с подписью)."""
    if message.photo:
        plain = message.caption or ""
        html = message.html_text or message.caption or ""
        photo_file_id = message.photo[-1].file_id
    else:
        plain = message.text or ""
        html = message.html_text or ""
        photo_file_id = None

    plain_body = plain[len(command):].lstrip()
    html_body = html[len(command):].lstrip()

    plain_parts = plain_body.split("\n", 1)
    html_parts = html_body.split("\n", 1)

    name = plain_parts[0].strip()
    content_html = html_parts[1].strip() if len(html_parts) > 1 else ""
    return name, content_html, photo_file_id


async def _is_admin_filter(message: Message):
    return await is_admin(message.from_user.id)


# ---------- /start и главное меню ----------

REFERRALS_PER_REWARD_DAY = 3


async def _maybe_award_referral_bonus(referrer_id: int):
    total = await count_referrals(referrer_id)
    due_rewards = total // REFERRALS_PER_REWARD_DAY
    given = await get_referral_rewards_given(referrer_id)
    if due_rewards <= given:
        return
    new_days = due_rewards - given
    await extend_subscription(referrer_id, new_days)
    await set_referral_rewards_given(referrer_id, due_rewards)
    try:
        await bot.send_message(
            referrer_id,
            f"🎉 Ты пригласил уже {total} человек! Начислили {new_days} дн. подписки бесплатно.",
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        pass


@dp.message(CommandStart())
async def cmd_start(message: Message):
    parts = message.text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else None

    referred_by = None
    if payload and payload.isdigit():
        candidate = int(payload)
        if candidate != message.from_user.id:
            referred_by = candidate

    was_created = await ensure_user(message.from_user.id, message.from_user.username, referred_by=referred_by)

    if was_created and referred_by:
        await _maybe_award_referral_bonus(referred_by)

    display_name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    text = await get_setting("welcome_text")
    try:
        text = text.format(name=display_name)
    except (KeyError, IndexError):
        pass  # в тексте могут быть свои фигурные скобки — не ломаем приветствие
    photo = await get_setting_photo("welcome_text")
    sent = await reply(message, text, photo_file_id=photo, reply_markup=await main_menu_kb())
    await set_welcome_msg_id(message.from_user.id, sent.message_id)


@dp.callback_query(F.data == "menu:search")
async def cb_search(callback: CallbackQuery):
    text = await get_setting("search_prompt_text")
    photo = await get_setting_photo("search_prompt_text")
    await reply(callback, text, photo_file_id=photo)
    await callback.answer()


@dp.callback_query(F.data == "menu:help")
async def cb_help(callback: CallbackQuery):
    text = await get_setting("help_text")
    photo = await get_setting_photo("help_text")
    await reply(callback, text, photo_file_id=photo)
    await callback.answer()


@dp.callback_query(F.data == "menu:referrals")
async def cb_referrals(callback: CallbackQuery):
    user_id = callback.from_user.id
    count = await count_referrals(user_id)
    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    template = await get_setting("referral_text")
    photo = await get_setting_photo("referral_text")
    await reply(callback, template.format(link=link, count=count), photo_file_id=photo)
    await callback.answer()


# ---------- админ: карты / билды (с предпросмотром) ----------

@dp.message(Command("addmap"), _is_admin_filter)
async def cmd_addmap(message: Message):
    name, content_html, photo_file_id = _extract_name_and_content_html(message, "/addmap")
    if not name or (not content_html and not photo_file_id):
        await reply(
            message,
            "Пришли одним сообщением (можно с фото):\nНазвание карты\nдальше — пики "
            "(текст, форматирование и платные эмодзи сохранятся)",
        )
        return
    exists = await entry_exists("maps", name)
    await set_pending_action(
        message.from_user.id,
        "addmap",
        {"name": name, "content_html": content_html, "photo_file_id": photo_file_id},
    )
    verb = "🔁 Обновить" if exists else "🔎 Предпросмотр"
    action_label = "✅ Обновить" if exists else "✅ Опубликовать"
    await reply(
        message,
        f"{verb} карту «{name}»:\n\n🗺 <b>{name}</b>\n\n{content_html}",
        photo_file_id=photo_file_id,
        reply_markup=action_confirm_kb(action_label, "❌ Отменить"),
    )


@dp.message(Command("addbuild"), _is_admin_filter)
async def cmd_addbuild(message: Message):
    name, content_html, photo_file_id = _extract_name_and_content_html(message, "/addbuild")
    if not name or (not content_html and not photo_file_id):
        await reply(
            message,
            "Пришли одним сообщением (можно с фото):\nНазвание бойца\nдальше — билд "
            "(текст, форматирование и платные эмодзи сохранятся)",
        )
        return
    exists = await entry_exists("builds", name)
    await set_pending_action(
        message.from_user.id,
        "addbuild",
        {"name": name, "content_html": content_html, "photo_file_id": photo_file_id},
    )
    verb = "🔁 Обновить" if exists else "🔎 Предпросмотр"
    action_label = "✅ Обновить" if exists else "✅ Опубликовать"
    await reply(
        message,
        f"{verb} билд «{name}»:\n\n⚔️ <b>{name}</b>\n\n{content_html}",
        photo_file_id=photo_file_id,
        reply_markup=action_confirm_kb(action_label, "❌ Отменить"),
    )


@dp.message(Command("delmap"), _is_admin_filter)
async def cmd_delmap(message: Message):
    name = message.text[len("/delmap"):].strip()
    if not name:
        await reply(message, "Формат: /delmap Название карты")
        return
    entry = await find_entry("maps", name)
    if not entry:
        await reply(message, f"Карта «{name}» не найдена.")
        return
    await set_pending_action(
        message.from_user.id, "delmap", {"search_key": entry["search_key"], "name": entry["name"]}
    )
    await reply(
        message,
        f"🗑 Удалить эту карту («{entry['name']}»)?\n\n{entry['content']}",
        photo_file_id=entry["photo_file_id"],
        reply_markup=action_confirm_kb("🗑 Удалить", "↩️ Оставить"),
    )


@dp.message(Command("delbuild"), _is_admin_filter)
async def cmd_delbuild(message: Message):
    name = message.text[len("/delbuild"):].strip()
    if not name:
        await reply(message, "Формат: /delbuild Название бойца")
        return
    entry = await find_entry("builds", name)
    if not entry:
        await reply(message, f"Билд на «{name}» не найден.")
        return
    await set_pending_action(
        message.from_user.id, "delbuild", {"search_key": entry["search_key"], "name": entry["name"]}
    )
    await reply(
        message,
        f"🗑 Удалить этот билд («{entry['name']}»)?\n\n{entry['content']}",
        photo_file_id=entry["photo_file_id"],
        reply_markup=action_confirm_kb("🗑 Удалить", "↩️ Оставить"),
    )


async def _reply_entry_list(message: Message, table: str, title: str):
    names = await list_entry_names(table)
    if not names:
        await reply(message, f"Пока нет ни одной записи ({title}).")
        return
    header = f"{title} — всего {len(names)}:\n\n"
    chunk = header
    for i, name in enumerate(names, 1):
        line = f"{i}. {name}\n"
        if len(chunk) + len(line) > 3800:
            await reply(message, chunk)
            chunk = ""
        chunk += line
    if chunk:
        await reply(message, chunk)


@dp.message(Command("listmaps"), _is_admin_filter)
async def cmd_listmaps(message: Message):
    await _reply_entry_list(message, "maps", "🗺 Карты")


@dp.message(Command("listbuilds"), _is_admin_filter)
async def cmd_listbuilds(message: Message):
    await _reply_entry_list(message, "builds", "⚔️ Билды")


# ---------- админ: контры бойцов (для драфт-помощника) ----------

@dp.message(Command("addcounter"), _is_admin_filter)
async def cmd_addcounter(message: Message):
    raw = message.text[len("/addcounter"):].strip()
    if not raw:
        await reply(
            message,
            "Пришли одним сообщением:\nИмя бойца\nдальше — через запятую, кого он контрит",
        )
        return
    name, content = _split_first_line(raw)
    counters_list = [c.strip() for c in content.split(",") if c.strip()]
    if not name or not counters_list:
        await reply(message, "Нужно имя бойца на первой строке и список через запятую дальше.")
        return
    await set_pending_action(message.from_user.id, "addcounter", {"name": name, "counters": counters_list})
    await reply(
        message,
        f"🔎 «{name}» контрит: {', '.join(counters_list)}\n\nСохранить?",
        reply_markup=action_confirm_kb("✅ Сохранить", "❌ Отменить"),
    )


@register_apply("addcounter")
async def _apply_addcounter(admin_id, payload):
    await set_counters(payload["name"], payload["counters"])
    return f"✅ Контры для «{payload['name']}» сохранены."


@dp.message(Command("delcounter"), _is_admin_filter)
async def cmd_delcounter(message: Message):
    name = message.text[len("/delcounter"):].strip()
    if not name:
        await reply(message, "Формат: /delcounter Имя бойца")
        return
    if not await counters_exists(name):
        await reply(message, f"Контры для «{name}» не найдены.")
        return
    await set_pending_action(message.from_user.id, "delcounter", {"name": name})
    await reply(
        message,
        f"Удалить контры для «{name}»?",
        reply_markup=action_confirm_kb("🗑 Удалить", "↩️ Оставить"),
    )


@register_apply("delcounter")
async def _apply_delcounter(admin_id, payload):
    await delete_counters(payload["name"])
    return f"✅ Контры для «{payload['name']}» удалены."


@dp.message(Command("listcounters"), _is_admin_filter)
async def cmd_listcounters(message: Message):
    rows = await list_all_counters()
    if not rows:
        await reply(message, "Пока не задано ни одной контры.")
        return
    lines = ["🥊 Контры бойцов:\n"]
    for r in rows:
        lines.append(f"{r['name']} → {r['counters']}")
    text = "\n".join(lines)
    for i in range(0, len(text), 3800):
        await reply(message, text[i:i + 3800])


@dp.message(Command("setmaptypeheroes"), _is_admin_filter)
async def cmd_setmaptypeheroes(message: Message):
    kb = InlineKeyboardBuilder()
    for mt in MAP_TYPES:
        kb.button(text=mt, callback_data=f"pickmaptype_admin:{mt}", style="success")
    kb.adjust(1)
    await reply(message, "Для какого типа карты задаём список сильных бойцов?", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("pickmaptype_admin:"))
async def cb_pickmaptype_admin(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    map_type = callback.data.split(":", 1)[1]
    current = await get_map_type_heroes(map_type)
    await set_pending_input(callback.from_user.id, f"args:maptypeheroes:{map_type}")
    current_note = f"\n\nСейчас: {', '.join(current)}" if current else ""
    await reply(
        callback,
        f"Пришли список бойцов, сильных на типе «{map_type}», через запятую.{current_note}",
    )
    await callback.answer()


async def _awaiting_maptypeheroes(message: Message):
    if not await is_admin(message.from_user.id):
        return False
    pending = await get_pending_input(message.from_user.id)
    return bool(pending and pending.startswith("args:maptypeheroes:"))


@dp.message(_awaiting_maptypeheroes)
async def receive_maptypeheroes(message: Message):
    pending = await get_pending_input(message.from_user.id)
    map_type = pending.split(":", 2)[2]
    await clear_pending_input(message.from_user.id)
    heroes_list = [h.strip() for h in (message.text or "").split(",") if h.strip()]
    if not heroes_list:
        await reply(message, "Нужен список через запятую. Попробуй снова: /setmaptypeheroes")
        return
    await set_pending_action(message.from_user.id, "setmaptypeheroes", {"map_type": map_type, "heroes": heroes_list})
    await reply(
        message,
        f"🔎 Сильные на «{map_type}»: {', '.join(heroes_list)}\n\nСохранить?",
        reply_markup=action_confirm_kb("✅ Сохранить", "❌ Отменить"),
    )


@register_apply("setmaptypeheroes")
async def _apply_setmaptypeheroes(admin_id, payload):
    await set_map_type_heroes(payload["map_type"], payload["heroes"])
    return f"✅ Список для «{payload['map_type']}» сохранён."


@register_apply("addmap")
async def _apply_addmap(admin_id, payload):
    await add_entry("maps", payload["name"], payload["content_html"], payload.get("photo_file_id"))
    search_key = payload["name"].strip().lower()
    kb = InlineKeyboardBuilder()
    for mt in MAP_TYPES:
        kb.button(text=mt, callback_data=f"maptype:{search_key}:{mt}", style="success")
    kb.adjust(1)
    await send_tracked(
        admin_id, f"Какой тип у карты «{payload['name']}»?", reply_markup=kb.as_markup()
    )
    return f"✅ Карта «{payload['name']}» опубликована."


@dp.callback_query(F.data.startswith("maptype:"))
async def cb_pick_map_type(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    _, search_key, map_type = callback.data.split(":", 2)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT name FROM maps WHERE search_key = ?", (search_key,))
        row = await cur.fetchone()
    if not row:
        await callback.message.edit_text("Карта не найдена (возможно, удалена).")
        await callback.answer()
        return
    await set_map_type(search_key, map_type)
    await callback.message.edit_text(f"✅ Тип карты «{row[0]}» — {map_type}.")
    await callback.answer()


@register_apply("addbuild")
async def _apply_addbuild(admin_id, payload):
    await add_entry("builds", payload["name"], payload["content_html"], payload.get("photo_file_id"))
    return f"✅ Билд на «{payload['name']}» опубликован."


@register_apply("delmap")
async def _apply_delmap(admin_id, payload):
    await delete_entry("maps", payload["search_key"])
    return f"✅ Карта «{payload['name']}» удалена."


@register_apply("delbuild")
async def _apply_delbuild(admin_id, payload):
    await delete_entry("builds", payload["search_key"])
    return f"✅ Билд «{payload['name']}» удалён."


@dp.callback_query(F.data.startswith("actconfirm:"))
async def cb_actconfirm(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    async def _edit(text):
        if callback.message.photo:
            await callback.message.edit_caption(caption=text)
        else:
            await callback.message.edit_text(text)

    answer = callback.data.split(":")[1]
    pending = await get_pending_action(callback.from_user.id)
    if not pending:
        await _edit("Нечего подтверждать — запрос устарел.")
        await callback.answer()
        return
    await clear_pending_action(callback.from_user.id)
    if answer == "yes":
        handler = APPLY_HANDLERS.get(pending["action_type"])
        result_text = await handler(callback.from_user.id, pending["payload"]) if handler else "✅ Готово"
        await _edit(result_text)
    else:
        await _edit("Отменено")
    await callback.answer()


# ---------- админ: пользователи, подписки, админы, рефералы ----------

@dp.message(Command("addsub"), _is_admin_filter)
async def cmd_addsub(message: Message):
    args = message.text.split()[1:]
    if len(args) == 2 and args[0].isdigit() and args[1].lstrip("-").isdigit():
        await _process_addsub(message, int(args[0]), int(args[1]))
        return
    await set_pending_input(message.from_user.id, "args:addsub")
    await reply(message, "Пришли: <user_id> <дней>\nНапример: 123456789 30")


async def _awaiting_args_addsub(message: Message):
    if not await is_admin(message.from_user.id):
        return False
    return await get_pending_input(message.from_user.id) == "args:addsub"


@dp.message(_awaiting_args_addsub)
async def receive_args_addsub(message: Message):
    await clear_pending_input(message.from_user.id)
    args = (message.text or "").split()
    if len(args) != 2 or not args[0].isdigit() or not args[1].lstrip("-").isdigit():
        await reply(message, "Не понял. Формат: <user_id> <дней>. Попробуй снова: /addsub")
        return
    await _process_addsub(message, int(args[0]), int(args[1]))


async def _process_addsub(message: Message, user_id: int, days: int):
    user = await get_user(user_id)
    if not user:
        await reply(message, "Пользователь с таким id не найден в базе (он ещё не писал боту).")
        return
    await set_pending_action(message.from_user.id, "addsub", {"user_id": user_id, "days": days})
    await reply(
        message,
        f"{_fmt_user_card(user)}\n\nВыдать подписку на {days} дн.?",
        reply_markup=action_confirm_kb("✅ Выдать", "❌ Отменить"),
    )


@dp.message(Command("delsub"), _is_admin_filter)
async def cmd_delsub(message: Message):
    args = message.text.split()[1:]
    if len(args) == 1 and args[0].isdigit():
        await _process_delsub(message, int(args[0]))
        return
    await set_pending_input(message.from_user.id, "args:delsub")
    await reply(message, "Пришли user_id пользователя, у которого нужно снять подписку.")


async def _awaiting_args_delsub(message: Message):
    if not await is_admin(message.from_user.id):
        return False
    return await get_pending_input(message.from_user.id) == "args:delsub"


@dp.message(_awaiting_args_delsub)
async def receive_args_delsub(message: Message):
    await clear_pending_input(message.from_user.id)
    text = (message.text or "").strip()
    if not text.isdigit():
        await reply(message, "Не понял. Нужен числовой user_id. Попробуй снова: /delsub")
        return
    await _process_delsub(message, int(text))


async def _process_delsub(message: Message, user_id: int):
    user = await get_user(user_id)
    if not user:
        await reply(message, "Пользователь с таким id не найден.")
        return
    await set_pending_action(message.from_user.id, "delsub", {"user_id": user_id})
    await reply(
        message,
        f"{_fmt_user_card(user)}\n\nСнять подписку?",
        reply_markup=action_confirm_kb("🗑 Снять", "↩️ Оставить"),
    )


@dp.message(Command("addadmin"), _is_admin_filter)
async def cmd_addadmin(message: Message):
    args = message.text.split()[1:]
    if len(args) == 1 and args[0].isdigit():
        await _process_addadmin(message, int(args[0]))
        return
    await set_pending_input(message.from_user.id, "args:addadmin")
    await reply(message, "Пришли user_id человека, которого нужно сделать админом.")


async def _awaiting_args_addadmin(message: Message):
    if not await is_admin(message.from_user.id):
        return False
    return await get_pending_input(message.from_user.id) == "args:addadmin"


@dp.message(_awaiting_args_addadmin)
async def receive_args_addadmin(message: Message):
    await clear_pending_input(message.from_user.id)
    text = (message.text or "").strip()
    if not text.isdigit():
        await reply(message, "Не понял. Нужен числовой user_id. Попробуй снова: /addadmin")
        return
    await _process_addadmin(message, int(text))


async def _process_addadmin(message: Message, user_id: int):
    await set_pending_action(message.from_user.id, "addadmin", {"user_id": user_id})
    await reply(
        message,
        f"Сделать {user_id} админом бота?",
        reply_markup=action_confirm_kb("✅ Сделать админом", "❌ Отменить"),
    )


@dp.message(Command("deladmin"), _is_admin_filter)
async def cmd_deladmin(message: Message):
    args = message.text.split()[1:]
    if len(args) == 1 and args[0].isdigit():
        await _process_deladmin(message, int(args[0]))
        return
    await set_pending_input(message.from_user.id, "args:deladmin")
    await reply(message, "Пришли user_id человека, которого нужно снять с админки.")


async def _awaiting_args_deladmin(message: Message):
    if not await is_admin(message.from_user.id):
        return False
    return await get_pending_input(message.from_user.id) == "args:deladmin"


@dp.message(_awaiting_args_deladmin)
async def receive_args_deladmin(message: Message):
    await clear_pending_input(message.from_user.id)
    text = (message.text or "").strip()
    if not text.isdigit():
        await reply(message, "Не понял. Нужен числовой user_id. Попробуй снова: /deladmin")
        return
    await _process_deladmin(message, int(text))


async def _process_deladmin(message: Message, user_id: int):
    await set_pending_action(message.from_user.id, "deladmin", {"user_id": user_id})
    await reply(
        message,
        f"Снять {user_id} с админки?",
        reply_markup=action_confirm_kb("🗑 Снять", "↩️ Оставить"),
    )


@register_apply("addsub")
async def _apply_addsub(admin_id, payload):
    new_until = await extend_subscription(payload["user_id"], payload["days"])
    return f"✅ Подписка для {payload['user_id']} продлена до {new_until[:16]} UTC"


@register_apply("delsub")
async def _apply_delsub(admin_id, payload):
    await revoke_subscription(payload["user_id"])
    return f"✅ Подписка для {payload['user_id']} снята"


@register_apply("addadmin")
async def _apply_addadmin(admin_id, payload):
    await add_admin(payload["user_id"])
    return f"✅ {payload['user_id']} теперь админ"


@register_apply("deladmin")
async def _apply_deladmin(admin_id, payload):
    await remove_admin(payload["user_id"])
    return f"✅ {payload['user_id']} больше не админ"


@dp.message(Command("users"), _is_admin_filter)
async def cmd_users(message: Message):
    args = message.text.split()[1:]
    if args and args[0].isdigit():
        user = await get_user(int(args[0]))
        if not user:
            await reply(message, "Пользователь с таким id не найден.")
            return
        await reply(message, _fmt_user_card(user))
        return
    total = await count_users()
    users = await list_users(0, USERS_PAGE_SIZE)
    if not users:
        await reply(message, "Пока нет пользователей.")
        return
    await reply(message, 
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
    args = message.text.split()[1:]
    if args and args[0].isdigit():
        user_id = int(args[0])
        invited = await count_referrals(user_id)
        purchased = await count_referral_purchases(user_id)
        await reply(
            message,
            f"👤 ID: {user_id}\nПривёл: {invited} чел.\nИз них купили подписку: {purchased} чел.",
        )
        return

    total = await referral_leaderboard_total()
    rows = await referral_leaderboard(0, USERS_PAGE_SIZE)
    if not rows:
        await reply(message, "Пока никто никого не пригласил.")
        return
    lines = [f"👥 Всего пригласивших: {total}\n"]
    for user_id, cnt, purchased_cnt in rows:
        lines.append(f"{user_id} — {cnt} чел. (купили: {purchased_cnt or 0})")
    await reply(
        message,
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
    for user_id, cnt, purchased_cnt in rows:
        lines.append(f"{user_id} — {cnt} чел. (купили: {purchased_cnt or 0})")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=users_pagination_kb("ref_page", offset, USERS_PAGE_SIZE, total),
    )
    await callback.answer()


# ---------- админ: рассылка ----------

@dp.message(Command("broadcast"), _is_admin_filter)
async def cmd_broadcast(message: Message):
    await set_pending_input(message.from_user.id, "broadcast")
    await reply(message, "Пришли сообщение для рассылки (текст, фото — что угодно).")


async def _awaiting_broadcast(message: Message):
    if not await is_admin(message.from_user.id):
        return False
    return await get_pending_input(message.from_user.id) == "broadcast"


@dp.message(_awaiting_broadcast)
async def receive_broadcast_content(message: Message):
    await clear_pending_input(message.from_user.id)
    _pending_broadcast_content[message.from_user.id] = message
    total = await count_users()
    await reply(message, 
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
            await reply(callback, f"Прогресс: {i}/{len(user_ids)}")
    await reply(callback, f"✅ Рассылка завершена. Отправлено: {sent}. Не доставлено: {failed}.")
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
    await reply(message, "\n".join(lines))


# ---------- админ: режим подписки ----------

@dp.message(Command("subscription"), _is_admin_filter)
async def cmd_subscription(message: Message):
    args = message.text.split()[1:]
    if not args or args[0] not in ("on", "off"):
        current = await is_subscription_enabled()
        await reply(
            message,
            f"Текущий режим: {'платный' if current else 'бесплатный (бета)'}.\nФормат: /subscription on|off",
        )
        return
    enable = args[0] == "on"
    await set_pending_action(message.from_user.id, "subtoggle", {"enabled": "1" if enable else "0"})
    label = "✅ Включить платный режим" if enable else "✅ Выключить платный режим"
    question = (
        "Включить платный режим? Пробный день выдаётся только тем, кто им ещё не пользовался."
        if enable
        else "Выключить платный режим? Доступ у всех станет бесплатным."
    )
    await reply(message, question, reply_markup=action_confirm_kb(label, "❌ Отменить"))


@register_apply("subtoggle")
async def _apply_subtoggle(admin_id, payload):
    await set_setting("subscription_enabled", payload["enabled"])
    if payload["enabled"] == "1":
        return "✅ Платный режим включён."
    return "✅ Платный режим выключен — доступ у всех бесплатный."


# ---------- админ: режим техработ ----------

@dp.message(Command("maintenance"), _is_admin_filter)
async def cmd_maintenance(message: Message):
    args = message.text.split()[1:]
    if not args or args[0] not in ("on", "off"):
        current = await is_maintenance_mode()
        await reply(
            message,
            f"Техработы сейчас: {'идут' if current else 'не идут'}.\nФормат: /maintenance on|off",
        )
        return
    enable = args[0] == "on"
    if enable and await is_maintenance_mode():
        await reply(message, "Техработы уже идут.")
        return
    if not enable and not await is_maintenance_mode():
        await reply(message, "Техработы и так не идут.")
        return
    await set_pending_action(message.from_user.id, "maintenance_toggle", {"enabled": enable})
    label = "🛠 Начать техработы" if enable else "✅ Завершить техработы"
    question = (
        "Включить режим техработ? Пока он включён, время подписки у пользователей не будет расходоваться."
        if enable
        else "Завершить техработы? Всем пользователям продлим подписку на время, пока шли техработы."
    )
    await reply(message, question, reply_markup=action_confirm_kb(label, "❌ Отменить"))


@register_apply("maintenance_toggle")
async def _apply_maintenance_toggle(admin_id, payload):
    if payload["enabled"]:
        await start_maintenance()
        return "🛠 Техработы начаты. Подписки заморожены."
    elapsed_seconds = await end_maintenance()
    hours = elapsed_seconds // 3600
    minutes = (elapsed_seconds % 3600) // 60
    return f"✅ Техработы завершены. Подписки продлены на {hours} ч. {minutes} мин."


# ---------- админ: массовая выдача подписки всем ----------

@dp.message(Command("addsuball"), _is_admin_filter)
async def cmd_addsuball(message: Message):
    args = message.text.split()[1:]
    if len(args) == 1 and args[0].lstrip("-").isdigit():
        await _process_addsuball(message, int(args[0]))
        return
    await set_pending_input(message.from_user.id, "args:addsuball")
    await reply(message, "На сколько дней выдать подписку всем пользователям? Пришли число.")


async def _awaiting_args_addsuball(message: Message):
    if not await is_admin(message.from_user.id):
        return False
    return await get_pending_input(message.from_user.id) == "args:addsuball"


@dp.message(_awaiting_args_addsuball)
async def receive_args_addsuball(message: Message):
    await clear_pending_input(message.from_user.id)
    text = (message.text or "").strip()
    if not text.lstrip("-").isdigit():
        await reply(message, "Не понял. Нужно число дней. Попробуй снова: /addsuball")
        return
    await _process_addsuball(message, int(text))


async def _process_addsuball(message: Message, days: int):
    total = await count_users()
    await set_pending_action(message.from_user.id, "addsuball", {"days": days})
    await reply(
        message,
        f"Выдать всем пользователям ({total} чел.) +{days} дн. подписки, с уведомлением каждому?",
        reply_markup=action_confirm_kb("✅ Выдать всем", "❌ Отменить"),
    )


@register_apply("addsuball")
async def _apply_addsuball(admin_id, payload):
    days = payload["days"]
    user_ids = await all_user_ids()
    ok, failed = 0, 0
    for user_id in user_ids:
        await extend_subscription(user_id, days)
        try:
            await bot.send_message(user_id, f"🎁 Вам начислено {days} дн. подписки!")
            ok += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        await asyncio.sleep(0.05)
    return f"✅ Подписка на {days} дн. выдана {len(user_ids)} пользователям. Уведомлены: {ok}, не доставлено: {failed}."


# ---------- админ: промокоды ----------

@dp.message(Command("addpromo"), _is_admin_filter)
async def cmd_addpromo(message: Message):
    await set_pending_input(message.from_user.id, "args:addpromo")
    await reply(
        message,
        "Пришли 5 строк:\n"
        "КОД\n"
        "тип (percent или days)\n"
        "значение (число: для percent — процент скидки, для days — дней бесплатно)\n"
        "лимит использований\n"
        "период \"остывания\" в днях (после использования этого кода — сколько дней нельзя "
        "использовать другой промокод)\n\n"
        "Например:\nSALE20\npercent\n20\n100\n0",
    )


async def _awaiting_args_addpromo(message: Message):
    if not await is_admin(message.from_user.id):
        return False
    return await get_pending_input(message.from_user.id) == "args:addpromo"


@dp.message(_awaiting_args_addpromo)
async def receive_args_addpromo(message: Message):
    await clear_pending_input(message.from_user.id)
    lines = (message.text or "").strip().split("\n")
    if len(lines) != 5:
        await reply(message, "Нужно ровно 5 строк. Попробуй снова: /addpromo")
        return
    code, type_, value_s, limit_s, cooldown_s = [line.strip() for line in lines]
    type_ = type_.lower()
    if type_ not in ("percent", "days") or not code:
        await reply(message, "Тип должен быть percent или days. Попробуй снова: /addpromo")
        return
    if not value_s.isdigit() or not limit_s.isdigit() or not cooldown_s.isdigit():
        await reply(message, "Значение, лимит и период должны быть числами. Попробуй снова: /addpromo")
        return
    value, limit, cooldown = int(value_s), int(limit_s), int(cooldown_s)
    await set_pending_action(
        message.from_user.id,
        "addpromo",
        {"code": code, "type": type_, "value": value, "limit": limit, "cooldown": cooldown},
    )
    type_label = "скидка %" if type_ == "percent" else "дней бесплатно"
    await reply(
        message,
        f"🔎 Промокод «{code.upper()}»\nТип: {type_label}\nЗначение: {value}\n"
        f"Лимит использований: {limit}\nОстывание: {cooldown} дн.\n\nСоздать?",
        reply_markup=action_confirm_kb("✅ Создать", "❌ Отменить"),
    )


@register_apply("addpromo")
async def _apply_addpromo(admin_id, payload):
    await create_promo(
        payload["code"], payload["type"], payload["value"], payload["limit"], payload["cooldown"]
    )
    return f"✅ Промокод «{payload['code'].upper()}» создан."


@dp.message(Command("delpromo"), _is_admin_filter)
async def cmd_delpromo(message: Message):
    code = message.text[len("/delpromo"):].strip()
    if not code:
        await reply(message, "Формат: /delpromo КОД")
        return
    promo = await get_promo(code)
    if not promo:
        await reply(message, f"Промокод «{code.upper()}» не найден.")
        return
    await set_pending_action(message.from_user.id, "delpromo", {"code": code})
    await reply(
        message,
        f"Удалить промокод «{code.upper()}»?",
        reply_markup=action_confirm_kb("🗑 Удалить", "↩️ Оставить"),
    )


@register_apply("delpromo")
async def _apply_delpromo(admin_id, payload):
    await delete_promo(payload["code"])
    return f"✅ Промокод «{payload['code'].upper()}» удалён."


@dp.message(Command("promos"), _is_admin_filter)
async def cmd_promos(message: Message):
    promos = await list_promos()
    if not promos:
        await reply(message, "Промокодов пока нет.")
        return
    lines = ["🎟 Промокоды:\n"]
    for p in promos:
        type_label = "%" if p["type"] == "percent" else "дней"
        lines.append(
            f"{p['code']} — {p['value']}{type_label}, "
            f"использован {p['uses_count']}/{p['uses_limit']}, остывание {p['cooldown_days']} дн."
        )
    await reply(message, "\n".join(lines))


# ---------- админ: экспорт статистики и график роста ----------

@dp.message(Command("export"), _is_admin_filter)
async def cmd_export(message: Message):
    import csv
    import io as _io
    from aiogram.types import BufferedInputFile

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users ORDER BY joined_at")
        rows = await cur.fetchall()

    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["user_id", "username", "joined_at", "trial_used", "sub_until", "has_purchased", "referred_by"]
    )
    for r in rows:
        writer.writerow(
            [r["user_id"], r["username"], r["joined_at"], r["trial_used"], r["sub_until"],
             r["has_purchased"], r["referred_by"]]
        )
    data = buf.getvalue().encode("utf-8-sig")
    doc = BufferedInputFile(data, filename="users_export.csv")
    await message.answer_document(doc, caption=f"Экспорт: {len(rows)} пользователей")


@dp.message(Command("growth"), _is_admin_filter)
async def cmd_growth(message: Message):
    import io as _io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from aiogram.types import BufferedInputFile

    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT substr(joined_at, 1, 10) as d, COUNT(*) FROM users GROUP BY d ORDER BY d"
        )
        rows = await cur.fetchall()

    if not rows:
        await reply(message, "Пока нет данных для графика.")
        return

    dates = [r[0] for r in rows]
    counts = [r[1] for r in rows]
    cumulative = []
    total = 0
    for c in counts:
        total += c
        cumulative.append(total)

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(dates, cumulative, marker="o", color="#2ecc71")
    ax.set_title("Рост пользователей")
    ax.set_ylabel("Всего пользователей")
    ax.tick_params(axis="x", rotation=60, labelsize=7)
    fig.tight_layout()

    buf = _io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)

    photo = BufferedInputFile(buf.read(), filename="growth.png")
    await message.answer_photo(photo, caption=f"Всего пользователей: {total}")


# ---------- админ: редактируемые тексты и кнопки (одна команда на каждую) ----------

def _extract_custom_emoji_icon(message: Message):
    """Если в сообщении есть один платный (custom) эмодзи — вернуть его id и
    оставшийся текст без символа этого эмодзи (он будет показан как иконка
    кнопки отдельно). offset/length у сущностей — в UTF-16, поэтому режем
    строку через UTF-16-кодирование, а не по python-индексам."""
    text = message.text or ""
    if not message.entities:
        return None, text
    for entity in message.entities:
        if entity.type == "custom_emoji":
            start, end = entity.offset, entity.offset + entity.length
            utf16 = text.encode("utf-16-le")
            before = utf16[: start * 2].decode("utf-16-le", errors="ignore")
            after = utf16[end * 2:].decode("utf-16-le", errors="ignore")
            new_text = (before + after).strip()
            return entity.custom_emoji_id, (new_text or text)
    return None, text


def _make_editable_handlers(key: str, command_name: str, prompt: str, action_type: str, label: str, allow_photo: bool, allow_icon: bool = False):
    async def cmd_handler(message: Message):
        await set_pending_input(message.from_user.id, f"{action_type}:{key}")
        await reply(message, prompt)

    async def awaiting_filter(message: Message):
        if not await is_admin(message.from_user.id):
            return False
        return await get_pending_input(message.from_user.id) == f"{action_type}:{key}"

    async def receive_handler(message: Message):
        await clear_pending_input(message.from_user.id)
        photo_file_id = None
        icon_id = None
        if allow_photo and message.photo:
            photo_file_id = message.photo[-1].file_id
            text = message.html_text or message.caption or ""
        elif allow_icon:
            icon_id, text = _extract_custom_emoji_icon(message)
        else:
            text = message.html_text or message.text or ""
        if not text and not photo_file_id:
            hint = "Нужен текст" + (" (можно с фото)" if allow_photo else "") + f". Попробуй снова: /{command_name}"
            await reply(message, hint)
            return
        await set_pending_action(
            message.from_user.id,
            action_type,
            {"key": key, "value": text, "photo_file_id": photo_file_id, "icon_id": icon_id},
        )
        if allow_icon and icon_id:
            preview_kb = InlineKeyboardBuilder()
            preview_kb.button(text=text, callback_data="noop", style="success", icon_custom_emoji_id=icon_id)
            preview_kb.button(text="✅ Сохранить", callback_data="actconfirm:yes", style="success")
            preview_kb.button(text="❌ Отменить", callback_data="actconfirm:no", style="danger")
            preview_kb.adjust(1, 2)
            await reply(
                message,
                f"🔎 Предпросмотр — {label} (с иконкой платного эмодзи):",
                reply_markup=preview_kb.as_markup(),
            )
        else:
            await reply(
                message,
                f"🔎 Предпросмотр — {label}:\n\n{text}",
                photo_file_id=photo_file_id,
                reply_markup=action_confirm_kb("✅ Сохранить", "❌ Отменить"),
            )

    dp.message.register(cmd_handler, Command(command_name), _is_admin_filter)
    dp.message.register(receive_handler, awaiting_filter)


@register_apply("settext")
async def _apply_settext(admin_id, payload):
    await set_setting(payload["key"], payload["value"])
    await set_setting_photo(payload["key"], payload.get("photo_file_id"))
    return "✅ Текст обновлён"


@register_apply("setbtn")
async def _apply_setbtn(admin_id, payload):
    await set_setting(payload["key"], payload["value"])
    await set_setting(f"{payload['key']}_icon", payload.get("icon_id") or "")
    return "✅ Название кнопки обновлено"


@dp.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


for _key, (_cmd, _prompt, _default) in EDITABLE_TEXTS.items():
    _make_editable_handlers(_key, _cmd, _prompt, "settext", "текст", allow_photo=True)

for _key, (_cmd, _prompt, _default) in BUTTON_LABELS.items():
    _make_editable_handlers(_key, _cmd, _prompt, "setbtn", "кнопка", allow_photo=False, allow_icon=True)


# ---------- админ: справка по командам ----------

@dp.message(Command("admin"), _is_admin_filter)
async def cmd_admin_help(message: Message):
    sections = ADMIN_COMMANDS_HELP.split("\n\n")
    chunk = ""
    for section in sections:
        candidate = (chunk + "\n\n" + section) if chunk else section
        if len(candidate) > 3800:
            await reply(message, chunk)
            chunk = section
        else:
            chunk = candidate
    if chunk:
        await reply(message, chunk)


# ---------- подписка / оплата (Telegram Stars) / промокоды ----------

INVOICE_PAYLOAD_PREFIX = "sub_"


@dp.callback_query(F.data == "menu:promo")
async def cb_enter_promo(callback: CallbackQuery):
    await set_pending_input(callback.from_user.id, "promo_code")
    await reply(callback, "Пришли промокод текстом.")
    await callback.answer()


async def _awaiting_promo_code(message: Message):
    return await get_pending_input(message.from_user.id) == "promo_code"


@dp.message(_awaiting_promo_code)
async def receive_promo_code(message: Message):
    await clear_pending_input(message.from_user.id)
    code = (message.text or "").strip()
    if not code:
        await reply(message, "Не понял код. Попробуй снова через «🎟 Ввести промокод».")
        return

    user_id = message.from_user.id
    promo = await get_promo(code)
    if not promo:
        await reply(message, "Такого промокода не существует.")
        return
    if promo["uses_count"] >= promo["uses_limit"]:
        await reply(message, "Этот промокод уже исчерпан.")
        return
    if await is_in_promo_cooldown(user_id):
        await reply(message, "Ты недавно уже использовал промокод — сейчас нельзя использовать ещё один.")
        return

    await increment_promo_uses(code)
    await set_promo_cooldown(user_id, promo["cooldown_days"])

    if promo["type"] == "percent":
        await set_pending_discount(user_id, promo["value"])
        await reply(
            message,
            f"✅ Промокод применён! Скидка {promo['value']}% сработает при следующей покупке подписки.",
            reply_markup=await buy_kb(),
        )
    else:  # days
        await extend_subscription(user_id, promo["value"])
        await reply(message, f"✅ Промокод применён! Начислено {promo['value']} дн. подписки.")


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
            photo = await get_setting_photo("sub_active_text")
            await reply(
                callback,
                template.format(days=left.days, hours=left.seconds // 3600),
                photo_file_id=photo,
            )
            await callback.answer()
            return
    text = await get_setting("no_access_text")
    photo = await get_setting_photo("no_access_text")
    await reply(callback, text, photo_file_id=photo, reply_markup=await buy_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery):
    tariff_key = callback.data.split(":", 1)[1]
    tariff = SUBSCRIPTION_TARIFFS.get(tariff_key)
    if not tariff:
        await callback.answer()
        return
    user = await get_user(callback.from_user.id)
    discount = user["pending_discount_percent"] if user else None
    display_stars = max(1, round(tariff["stars"] * (100 - discount) / 100)) if discount else tariff["stars"]

    await set_pending_action(callback.from_user.id, "confirm_purchase", {"tariff_key": tariff_key})
    template = await get_setting("purchase_confirm_text")
    text = template.format(days=tariff["days"], stars=display_stars)
    await reply(callback, text, reply_markup=action_confirm_kb("✅ Купить", "❌ Отменить"))
    await callback.answer()


@register_apply("confirm_purchase")
async def _apply_confirm_purchase(user_id, payload):
    tariff = SUBSCRIPTION_TARIFFS.get(payload["tariff_key"])
    if not tariff:
        return "Ошибка: тариф не найден."
    stars = tariff["stars"]
    user = await get_user(user_id)
    discount = user["pending_discount_percent"] if user else None
    if discount:
        stars = max(1, round(stars * (100 - discount) / 100))
        await clear_pending_discount(user_id)
    await bot.send_invoice(
        chat_id=user_id,
        title="Подписка на бота",
        description=f"{tariff['days']} дней доступа к пикам и билдам"
        + (f" (промокод: -{discount}%)" if discount else ""),
        payload=f"{INVOICE_PAYLOAD_PREFIX}{payload['tariff_key']}",
        currency="XTR",
        prices=[LabeledPrice(label="Подписка", amount=stars)],
        provider_token="",
    )
    return "💳 Открываю оплату..."


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    tariff_key = payload.replace(INVOICE_PAYLOAD_PREFIX, "", 1)
    tariff = SUBSCRIPTION_TARIFFS.get(tariff_key, SUBSCRIPTION_TARIFFS["30"])
    days = tariff["days"]

    await extend_subscription(message.from_user.id, days)
    await mark_purchased(message.from_user.id)

    stars_paid = message.successful_payment.total_amount
    for admin_id in await get_all_admin_ids():
        try:
            await bot.send_message(
                admin_id,
                f"💰 Новая оплата!\nID: {message.from_user.id}\n"
                f"Тариф: {days} дней\nСумма: {stars_paid}⭐",
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            pass

    template = await get_setting("payment_success_text")
    photo = await get_setting_photo("payment_success_text")
    await reply(message, template.format(days=days), photo_file_id=photo)


# ---------- драфт-помощник (регистрируем ДО общего поиска) ----------

async def draft_controls_kb():
    kb = InlineKeyboardBuilder()
    kb.button(
        text=await get_setting("btn_draft_exit"),
        callback_data="draft:exit",
        style="danger",
        icon_custom_emoji_id=await get_setting("btn_draft_exit_icon") or None,
    )
    kb.adjust(1)
    return kb.as_markup()


def _norm(s):
    return s.strip().lower()


async def _compute_draft_recommendation(enemies, banned, map_type):
    """Возвращает текст с рекомендацией контрпика (топ-3), учитывая
    контры, тип карты и баны."""
    if not enemies:
        return "Пока не назван ни один вражеский боец."

    banned_norm = {_norm(b) for b in banned}
    enemies_norm = [_norm(e) for e in enemies]

    # собираем весь пул известных боту бойцов: из контр и из билдов
    counters_rows = await list_all_counters()
    pool = {r["name"] for r in counters_rows}
    build_names = await list_entry_names("builds")
    pool.update(build_names)

    map_strong = set(_norm(h) for h in (await get_map_type_heroes(map_type))) if map_type else set()

    candidates = []
    for hero in pool:
        hero_norm = _norm(hero)
        if hero_norm in banned_norm or hero_norm in enemies_norm:
            continue
        hero_counters = {_norm(c) for c in await get_counters(hero)}
        beats = sum(1 for e in enemies_norm if e in hero_counters)
        loses_to = 0
        for e in enemies:
            enemy_counters = {_norm(c) for c in await get_counters(e)}
            if hero_norm in enemy_counters:
                loses_to += 1
        map_bonus = 1 if hero_norm in map_strong else 0
        score = beats - loses_to + map_bonus
        candidates.append((hero, score, beats))

    strong = [c for c in candidates if c[2] >= 1]
    fallback = not strong
    pool_to_rank = strong if strong else candidates
    pool_to_rank.sort(key=lambda x: x[1], reverse=True)
    top = pool_to_rank[:3]

    lines = []
    if fallback:
        lines.append("⚠️ Чистого контрпика нет, вот компромиссные варианты:\n")
    else:
        lines.append("🎯 Рекомендации:\n")
    for i, (hero, score, beats) in enumerate(top, 1):
        sign = "+" if score >= 0 else ""
        lines.append(f"{i}. {hero} ({sign}{score})")
    if not top:
        lines.append("Нет доступных вариантов (все забанены или уже взяты).")
    return "\n".join(lines)


@dp.callback_query(F.data == "menu:draft")
async def cb_draft_start(callback: CallbackQuery):
    await start_draft_session(callback.from_user.id)
    text = await get_setting("draft_intro_text")
    await reply(callback, text, reply_markup=await draft_controls_kb())
    await callback.answer()


@dp.callback_query(F.data == "draft:exit")
async def cb_draft_exit(callback: CallbackQuery):
    await end_draft_session(callback.from_user.id)
    await reply(callback, "❌ Вышел из режима драфта. Можно снова искать карты и билды как обычно.")
    await callback.answer()


async def _in_draft_session(message: Message):
    return await get_draft_session(message.from_user.id) is not None


@dp.message(_in_draft_session)
async def handle_draft_message(message: Message):
    session = await get_draft_session(message.from_user.id)
    stage = session["stage"]
    text = (message.text or "").strip()
    if not text:
        await reply(message, "Пришли текстом.", reply_markup=await draft_controls_kb())
        return

    if stage == "await_map_bans":
        lines = text.split("\n", 1)
        map_name = lines[0].strip()
        bans_raw = lines[1] if len(lines) > 1 else ""
        banned = [b.strip() for b in re.split(r"[,\n]+", bans_raw) if b.strip()]

        map_entry = await find_entry("maps", map_name)
        map_type = map_entry["map_type"] if map_entry and map_entry["map_type"] else None
        await update_draft_session(
            message.from_user.id, stage="picks", map_type=map_type, banned=json.dumps(banned)
        )

        parts = []
        if map_entry:
            parts.append(f"🗺 <b>{map_entry['name']}</b>\n\n{map_entry['content']}")
        else:
            parts.append(f"Карта «{map_name}» не найдена в базе — пики по ней не покажу.")

        if banned:
            parts.append(f"\n🚫 Забанены: {', '.join(banned)}")
        else:
            parts.append("\n⚠️ Баны не распознаны (пришли их со второй строки через запятую) — учитываю пустой список.")

        banned_norm = {_norm(b) for b in banned}
        fp_pool = await get_map_type_heroes(map_type) if map_type else []
        fp_available = [h for h in fp_pool if _norm(h) not in banned_norm]
        fp_header = await get_setting("draft_fp_header_text")
        if map_type and fp_available:
            parts.append(f"\n{fp_header}\n" + ", ".join(fp_available))
        elif map_type:
            parts.append(f"\n{fp_header}\nВсе сильные на этом типе карты бойцы забанены.")
        else:
            parts.append("\nТип карты неизвестен — ФП-рекомендации недоступны для этой карты.")

        pick_prompt = await get_setting("draft_pick_prompt_text")
        parts.append(f"\n{pick_prompt}")

        await reply(message, "\n".join(parts), reply_markup=await draft_controls_kb(), photo_file_id=map_entry["photo_file_id"] if map_entry else None)
        return

    # stage == "picks"
    banned = json.loads(session["banned"] or "[]")
    enemies = json.loads(session["enemies"] or "[]")

    if _norm(text) in {_norm(b) for b in banned}:
        await reply(message, f"«{text}» забанен — враг не может его взять.", reply_markup=await draft_controls_kb())
        return
    if len(enemies) >= 3:
        await reply(
            message,
            "Уже учтено 3 вражеских бойца — это максимум для 3v3. Открой «🧩 Драфт» заново для новой игры.",
            reply_markup=await draft_controls_kb(),
        )
        return

    enemies.append(text)
    await update_draft_session(message.from_user.id, enemies=json.dumps(enemies))
    recommendation = await _compute_draft_recommendation(enemies, banned, session["map_type"])
    await reply(
        message,
        f"Вражеская команда: {', '.join(enemies)}\n\n{recommendation}",
        reply_markup=await draft_controls_kb(),
    )


# ---------- поиск (общий обработчик текста, регистрируем последним) ----------

async def _not_pending_admin_input(message: Message):
    if not await is_admin(message.from_user.id):
        return True
    pending = await get_pending_input(message.from_user.id)
    return pending is None


@dp.message(F.text, _not_pending_admin_input)
async def handle_search(message: Message):
    query = message.text.strip()
    if not query or query.startswith("/"):
        return

    await ensure_user(message.from_user.id, message.from_user.username)
    user_id = message.from_user.id
    user = await get_user(user_id)

    if await is_subscription_enabled() and user and not user["trial_used"] and not await is_admin(user_id):
        await start_trial(user_id)

    if not await has_access(user_id):
        text = await get_setting("no_access_text")
        photo = await get_setting_photo("no_access_text")
        await reply(message, text, photo_file_id=photo, reply_markup=await buy_kb())
        return

    map_entry = await find_entry("maps", query)
    build_entry = await find_entry("builds", query)

    if not map_entry and not build_entry:
        text = await get_setting("not_found_text")
        photo = await get_setting_photo("not_found_text")
        await reply(message, text, photo_file_id=photo)
        return

    if map_entry:
        await log_query("map", map_entry["name"])
        await reply(
            message,
            f"🗺 <b>{map_entry['name']}</b>\n\n{map_entry['content']}",
            photo_file_id=map_entry["photo_file_id"],
        )
    if build_entry:
        await log_query("build", build_entry["name"])
        await reply(
            message,
            f"⚔️ <b>{build_entry['name']}</b>\n\n{build_entry['content']}",
            photo_file_id=build_entry["photo_file_id"],
        )


# ============================== ФОНОВЫЕ ЗАДАЧИ / ЗАПУСК ==============================

async def reminder_loop():
    while True:
        try:
            users = await users_needing_reminder(hours_ahead=24)
            template = await get_setting("reminder_text")
            photo = await get_setting_photo("reminder_text")
            for user in users:
                try:
                    if photo:
                        await bot.send_photo(user["user_id"], photo, caption=template)
                    else:
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
