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
}

# ---- редактируемые названия кнопок главного меню ----

BUTTON_LABELS = {
    "btn_search": ("setbtnsearch", "Пришли новое название кнопки поиска.", "🔍 Пики / Билды"),
    "btn_subscription": ("setbtnsub", "Пришли новое название кнопки подписки.", "💎 Моя подписка"),
    "btn_referrals": ("setbtnref", "Пришли новое название кнопки рефералов.", "👥 Мои рефералы"),
    "btn_help": ("setbtnhelp", 'Пришли новое название кнопки "Как пользоваться".', "ℹ️ Как пользоваться"),
    "btn_promo": ("setbtnpromo", 'Пришли новое название кнопки "Ввести промокод".', "🎟 Ввести промокод"),
    "btn_find_maps": ("setbtnfindmap", 'Пришли новое название кнопки "Найти карту".', "🗺 Найти карту"),
    "btn_find_builds": ("setbtnfindbuild", 'Пришли новое название кнопки "Найти билд".', "⚔️ Найти билд"),
    "btn_find_counters": (
        "setbtnfindcounter",
        'Пришли новое название кнопки "Найти контру".',
        "🛡 Найти контру",
    ),
}

# ---- бойцы и их контры (предзаполняется в базу при первом запуске) ----

DEFAULT_COUNTERS = {
    "Шелли": ["Вольт", "Отис", "Гейл"],
    "Кольт": ["Мина", "Сту", "Брок"],
    "Нита": ["Грифф", "Дамиан", "Отис"],
    "Булл": ["Вольт", "Грифф", "Мина"],
    "Брок": ["Сту", "Пирс", "Мортис"],
    "Эль Примо": ["Вольт", "Гейл", "Эмз"],
    "Барли": ["Эдгар", "Мортис", "Мико"],
    "Поко": ["Отис", "Эдгар", "Грифф"],
    "Роза": ["Отис", "Гейл", "Эмз"],
    "Рико": ["Эдгар", "Мортис", "Брок"],
    "Джесси": ["Брок", "Мортис", "Эдгар"],
    "Дэррил": ["Гейл", "Отис", "Коллет"],
    "Пенни": ["Брок", "Леон"],
    "Карл": ["Гейл", "Отис", "Чарли"],
    "8-БИТ": ["Пирс", "Бо"],
    "Джекки": ["Гейл", "Эмз", "Коллет"],
    "Гас": ["Сту", "Леон", "Мортис"],
    "Бо": ["Сту", "Кольт", "Болт"],
    "Тик": ["Мортис", "Мико", "Эдгар"],
    "Эмз": ["Ворон", "Мортис", "Гэйл"],
    "Сту": ["Гавс", "Чарли", "Мэг"],
    "Пайпер": ["Мортис", "Леон", "Эдгар"],
    "Пэм": ["Эдгар", "Коллет", "Гейл"],
    "Фрэнк": ["Отис", "Вольт", "Эдгар"],
    "Биби": ["Вольт", "Булл", "Отис"],
    "Беа": ["Мортис", "Эдгар", "Леон"],
    "Нани": ["Макс", "Брок", "Менди"],
    "Эдгар": ["Сту", "Перл", "Отис"],
    "Грифф": ["Сту", "Макс", "Отис"],
    "Гром": ["Эдгар", "Леон", "Мортис"],
    "Бонни": ["Брок", "Нани", "Анджело"],
    "Гэйл": ["Отис", "Лу", "Вольт"],
    "Колетт": ["Сту", "Отис", "Грифф"],
    "Белль": ["Пайпер", "Леон", "Байрон"],
    "Эш": ["Гейл", "Эмз", "Коллет"],
    "Лола": ["Джесси", "Скуик", "Пенни"],
    "Сэм": ["Вольт", "Отис", "Шелли"],
    "Мэнди": ["Макс", "Брок", "Болт"],
    "Мэйси": ["Макс", "Сту", "Мипл"],
    "Хэнк": ["Корделиус", "Вольт", "Р-Т"],
    "Перл": ["Грифф", "Отис", "Коллет"],
    "Ларри и Лори": ["Мико", "Мортис", "Эдгар"],
    "Анджело": ["Гавс", "Менди", "Чарли"],
    "Джуджу": ["Мортис", "Эдгар", "Мико"],
    "Даг": ["Гейл", "Луми", "Отис"],
    "Чарли": ["Скуик", "Спраут", "Амбер"],
    "Мортис": ["Вольт", "Грифф", "Отис"],
    "Тара": ["Джанет", "Бастер", "Сенди"],
    "Джин": ["Чарли", "Ева", "Мистер П"],
    "Макс": ["Лола", "Ворон", "Гейл"],
    "Мистер П": ["Эдгар", "Мортис", "Карл"],
    "Спраут": ["Мико", "Эдгар", "Мортис"],
    "Байрон": ["Мортис", "Эдгар", "Мико"],
    "Сквик": ["Мортис", "Леон", "Эдгар"],
    "Лу": ["Мэг", "Карл", "Поко (с гаджетом катарсис)"],
    "Базз": ["Мина", "Корделиус", "Чарли"],
    "Фэнг": ["Чарли", "Отис", "Лу"],
    "Ева": ["Брок", "Тик", "Амбер"],
    "Гавс": ["Мортис", "Эдгар", "Карл"],
    "Корделиус": ["Луми", "Мэг", "Бастер"],
    "Честер": ["Сириус", "Чарли", "Гавс"],
    "Мико": ["Корделиус", "Чарли", "Отис"],
    "Мелоди": ["Амбер", "Спайк", "Честер"],
    "Лили": ["Отис", "Перл", "Честер"],
    "Кит": ["Шелли", "Вольт", "Мэг"],
    "Драко": ["Спайк", "Эмз", "Луми"],
    "Берри": ["Эдгар", "Мико", "Мортис"],
    "Кенджи": ["Отис", "Корделиус", "Грифф"],
    "Мо": ["Мипл", "Вольт", "Отис"],
    "Грей": ["Эдгар", "Мортис", "Мико"],
    "Виллоу": ["Мико", "Эдгар", "Мортис"],
    "Отис": ["Сту", "Поко (с гаджетом катарсис)", "Диномайк"],
    "Бастер": ["Базз", "Дерилл", "Демиан"],
    "Р-Т": ["Брок", "Пайпер", "Сту"],
    "Сириус": ["Пенни", "Эмз", "Мортис"],
    "Нори": ["Отис", "Грифф", "Шелли"],
    "Мина": ["Отис", "Нита", "Чарли"],
    "Люми": ["Сту", "Кольт", "Пирс"],
    "Пирс": ["Мортис", "Эдгар", "Мико"],
    "Алли": ["Отис", "Мэг", "Кенджи"],
    "Дамиан": ["Эмз", "Отис", "Булл"],
    "Старр Нова": ["Вольт", "Грифф", "Гейл"],
    "Болт": ["Гейл", "Демиан", "Шелли"],
    "Глоуи": ["Грифф", "Диномайк", "Мортис"],
    "Венди": ["Корделиус", "Эдгар", "Брок"],
    "Зигги": ["Мортис", "Эдгар", "Мико"],
    "Наиджа": ["Мортис", "Леон", "Эдгар"],
    "Шейд": ["Мипл", "Кит", "Брок/Кольт/Фрэнк (раскрытие)"],
    "Финкс": ["Кит", "Кенджи", "Дерилл"],
    "Олли": ["Коллет", "Вольт", "Поко (с гаджетом катарсис)"],
}

ADMIN_COMMANDS_HELP = """
🛠 /panel — открыть админ-панель с инлайн-кнопками (тексты, кнопки, карты, билды, контры, пользователи, промокоды, настройки, админы) — то же самое, что команды ниже, но без набора текста.

🗺 <b>Карты и билды</b>
/addmap — добавить или обновить карту. Первая строка сообщения — название карты (по нему её ищут пользователи), всё что дальше — содержимое (пики); можно с форматированием, платными эмодзи и фото.
/delmap Название — удалить карту. Бот покажет её содержимое и попросит подтвердить.
/addbuild — то же самое, но для билда бойца (первая строка — имя бойца).
/delbuild Имя — удалить билд бойца, с подтверждением.
/listmaps — список всех карт, которые сейчас есть в базе.
/listbuilds — список всех билдов, которые сейчас есть в базе.

🛡 <b>Контры бойцов</b>
/addcounter — добавить или обновить контры бойца. Первая строка — имя бойца, дальше — список контрящих его бойцов; можно с форматированием и фото. База уже предзаполнена стандартным набором контр при первом запуске.
/delcounter Имя — удалить контры бойца, с подтверждением.
/listcounters — список всех бойцов, для которых заданы контры.

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

🗄 <b>Резервная копия базы</b>
/exportdb — выгрузить весь файл базы данных (пользователи, карты, билды, тексты, кнопки, промокоды, настройки — всё).
/importdb — пришли файл из /exportdb документом с подписью /importdb, чтобы полностью заменить текущую базу данных (с подтверждением, действие необратимо).

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

🔘 <b>Названия кнопок меню</b> (пришли платный эмодзи вместе с текстом — станет иконкой)
/setbtnsearch — "Пики / Билды"
/setbtnsub — "Моя подписка"
/setbtnref — "Мои рефералы"
/setbtnhelp — "Как пользоваться"
/setbtnpromo — "Ввести промокод"
/setbtnfindmap — "Найти карту"
/setbtnfindbuild — "Найти билд"
/setbtnfindcounter — "Найти контру"

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
CREATE TABLE IF NOT EXISTS counters (
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
    last_bot_msg_ids TEXT,
    search_flow_msg_ids TEXT
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
"""


async def _ensure_column(db, table, column, coltype):
    cur = await db.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in await cur.fetchall()]
    if column not in cols:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Если в базе уже была таблица counters с другой структурой (например,
        # от стороннего инструмента или прерванной миграции) — CREATE TABLE
        # IF NOT EXISTS её не тронет и вставки будут падать. Убираем её с
        # дороги, не удаляя данные, чтобы наша таблица создалась с нуля.
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='counters'"
        )
        if await cur.fetchone():
            cur = await db.execute("PRAGMA table_info(counters)")
            cols = [row[1] for row in await cur.fetchall()]
            if not {"search_key", "name", "content"}.issubset(cols):
                legacy = await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='counters_legacy'"
                )
                if await legacy.fetchone():
                    await db.execute("DROP TABLE counters")
                else:
                    await db.execute("ALTER TABLE counters RENAME TO counters_legacy")
                await db.commit()

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
        await _ensure_column(db, "builds", "photo_file_id", "TEXT")
        await _ensure_column(db, "chat_state", "search_flow_msg_ids", "TEXT")
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
        cur = await db.execute("SELECT COUNT(*) FROM counters")
        (counters_count,) = await cur.fetchone()
        if counters_count == 0:
            for name, counters in DEFAULT_COUNTERS.items():
                content = "\n".join(f"🔻 {c}" for c in counters)
                await db.execute(
                    "INSERT OR IGNORE INTO counters (search_key, name, content, photo_file_id) "
                    "VALUES (?, ?, ?, NULL)",
                    (name.strip().lower(), name, content),
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


async def get_search_flow_ids(user_id):
    state = await get_chat_state(user_id)
    if not state or not state["search_flow_msg_ids"]:
        return []
    try:
        return json.loads(state["search_flow_msg_ids"])
    except (TypeError, ValueError):
        return []


async def set_search_flow_ids(user_id, ids):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO chat_state (user_id, welcome_msg_id, last_user_msg_id, last_bot_msg_ids, "
            "search_flow_msg_ids) VALUES (?, NULL, NULL, NULL, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET search_flow_msg_ids = excluded.search_flow_msg_ids",
            (user_id, json.dumps(ids)),
        )
        await db.commit()


# ============================== KEYBOARDS ==============================

async def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(
        text=await get_setting("btn_search"),
        callback_data="menu:search",
        style="success",
        icon_custom_emoji_id=await get_setting("btn_search_icon") or None,
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
PANEL_ENTRY_PAGE_SIZE = 8
PANEL_USERS_PAGE_SIZE = 10
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

        # Раздел "найти карту/билд/контру" — переписка внутри него не чистится
        # после каждого шага, а копится и удаляется разом, когда пользователь
        # явно нажимает "◀️ В меню".
        is_flow_step = False
        if user_id and not is_start:
            if is_message:
                pending = await get_pending_input(user_id)
                is_flow_step = bool(pending and pending.startswith("findtype:"))
            elif isinstance(event, CallbackQuery):
                is_flow_step = bool(event.data and event.data.startswith("menu:findtype:"))

        to_delete = []
        if user_id and not is_start:
            to_delete = await get_deletable_ids(user_id)
            if is_flow_step:
                flow_ids = await get_search_flow_ids(user_id)
                flow_ids.extend(to_delete)
                await set_search_flow_ids(user_id, flow_ids)
                to_delete = []
            else:
                flow_ids = await get_search_flow_ids(user_id)
                if flow_ids:
                    to_delete.extend(flow_ids)
                    await set_search_flow_ids(user_id, [])

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


async def _is_admin_cb_filter(callback: CallbackQuery):
    return await is_admin(callback.from_user.id)


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


FIND_TYPE_PROMPTS = {
    "maps": "🗺 Напиши название карты, которую ищешь.",
    "builds": "⚔️ Напиши имя бойца, чтобы посмотреть его билд.",
    "counters": "🛡 Напиши имя бойца, чтобы посмотреть его контры.",
}

FIND_TYPE_LOG = {"maps": "map", "builds": "build", "counters": "counter"}


def _format_entry_reply(table, entry):
    if table == "maps":
        return f"🗺 <b>{entry['name']}</b>\n\n{entry['content']}"
    if table == "builds":
        return f"⚔️ <b>{entry['name']}</b>\n\n{entry['content']}"
    return f"🛡 <b>Контры на {entry['name']}</b>\n\n{entry['content']}"


async def find_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(
        text=await get_setting("btn_find_maps"),
        callback_data="menu:findtype:maps",
        style="success",
        icon_custom_emoji_id=await get_setting("btn_find_maps_icon") or None,
    )
    kb.button(
        text=await get_setting("btn_find_builds"),
        callback_data="menu:findtype:builds",
        style="success",
        icon_custom_emoji_id=await get_setting("btn_find_builds_icon") or None,
    )
    kb.button(
        text=await get_setting("btn_find_counters"),
        callback_data="menu:findtype:counters",
        style="success",
        icon_custom_emoji_id=await get_setting("btn_find_counters_icon") or None,
    )
    kb.adjust(1)
    return kb.as_markup()


@dp.callback_query(F.data == "menu:search")
async def cb_search(callback: CallbackQuery):
    await clear_pending_input(callback.from_user.id)
    text = await get_setting("search_prompt_text")
    photo = await get_setting_photo("search_prompt_text")
    await reply(callback, text, photo_file_id=photo, reply_markup=await find_menu_kb())
    await callback.answer()


def back_to_search_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ В меню", callback_data="menu:search", style="primary")
    return kb.as_markup()


@dp.callback_query(F.data.startswith("menu:findtype:"))
async def cb_find_type(callback: CallbackQuery):
    table = callback.data.split(":", 2)[2]
    if table not in FIND_TYPE_PROMPTS:
        await callback.answer()
        return
    await set_pending_input(callback.from_user.id, f"findtype:{table}")
    await reply(callback, FIND_TYPE_PROMPTS[table], reply_markup=back_to_search_kb())
    await callback.answer()


async def _awaiting_findtype(message: Message):
    pending = await get_pending_input(message.from_user.id)
    return bool(pending) and pending.startswith("findtype:")


@dp.message(F.text, _awaiting_findtype)
async def receive_findtype_query(message: Message):
    pending = await get_pending_input(message.from_user.id)
    table = pending.split(":", 1)[1]

    query = message.text.strip()
    if not query or query.startswith("/"):
        return

    await ensure_user(message.from_user.id, message.from_user.username)
    user_id = message.from_user.id
    user = await get_user(user_id)

    if await is_subscription_enabled() and user and not user["trial_used"] and not await is_admin(user_id):
        await start_trial(user_id)

    if not await has_access(user_id):
        await clear_pending_input(user_id)
        text = await get_setting("no_access_text")
        photo = await get_setting_photo("no_access_text")
        await reply(message, text, photo_file_id=photo, reply_markup=await buy_kb())
        return

    entry = await find_entry(table, query)
    if not entry:
        # Не сбрасываем pending_input — пользователь может сразу написать
        # другое имя, не нажимая заново кнопку категории.
        text = await get_setting("not_found_text")
        photo = await get_setting_photo("not_found_text")
        await reply(message, text, photo_file_id=photo, reply_markup=back_to_search_kb())
        return

    # pending_input остаётся "findtype:{table}" — пользователь может сразу
    # написать следующее имя в той же категории, без повторного нажатия
    # кнопки. Сессия завершается только по кнопке "◀️ В меню".
    await log_query(FIND_TYPE_LOG[table], entry["name"])
    await reply(
        message,
        _format_entry_reply(table, entry),
        photo_file_id=entry["photo_file_id"],
        reply_markup=back_to_search_kb(),
    )


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


@dp.message(Command("addcounter"), _is_admin_filter)
async def cmd_addcounter(message: Message):
    name, content_html, photo_file_id = _extract_name_and_content_html(message, "/addcounter")
    if not name or (not content_html and not photo_file_id):
        await reply(
            message,
            "Пришли одним сообщением (можно с фото):\nИмя бойца\nдальше — его контры "
            "(текст, форматирование и платные эмодзи сохранятся)",
        )
        return
    exists = await entry_exists("counters", name)
    await set_pending_action(
        message.from_user.id,
        "addcounter",
        {"name": name, "content_html": content_html, "photo_file_id": photo_file_id},
    )
    verb = "🔁 Обновить" if exists else "🔎 Предпросмотр"
    action_label = "✅ Обновить" if exists else "✅ Опубликовать"
    await reply(
        message,
        f"{verb} контры «{name}»:\n\n🛡 <b>{name}</b>\n\n{content_html}",
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


@dp.message(Command("delcounter"), _is_admin_filter)
async def cmd_delcounter(message: Message):
    name = message.text[len("/delcounter"):].strip()
    if not name:
        await reply(message, "Формат: /delcounter Имя бойца")
        return
    entry = await find_entry("counters", name)
    if not entry:
        await reply(message, f"Контры для «{name}» не найдены.")
        return
    await set_pending_action(
        message.from_user.id, "delcounter", {"search_key": entry["search_key"], "name": entry["name"]}
    )
    await reply(
        message,
        f"🗑 Удалить контры для «{entry['name']}»?\n\n{entry['content']}",
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


@dp.message(Command("listcounters"), _is_admin_filter)
async def cmd_listcounters(message: Message):
    await _reply_entry_list(message, "counters", "🛡 Контры")


@register_apply("addmap")
async def _apply_addmap(admin_id, payload):
    await add_entry("maps", payload["name"], payload["content_html"], payload.get("photo_file_id"))
    return f"✅ Карта «{payload['name']}» опубликована."


@register_apply("addbuild")
async def _apply_addbuild(admin_id, payload):
    await add_entry("builds", payload["name"], payload["content_html"], payload.get("photo_file_id"))
    return f"✅ Билд на «{payload['name']}» опубликован."


@register_apply("addcounter")
async def _apply_addcounter(admin_id, payload):
    await add_entry("counters", payload["name"], payload["content_html"], payload.get("photo_file_id"))
    return f"✅ Контры для «{payload['name']}» опубликованы."


@register_apply("delmap")
async def _apply_delmap(admin_id, payload):
    await delete_entry("maps", payload["search_key"])
    return f"✅ Карта «{payload['name']}» удалена."


@register_apply("delbuild")
async def _apply_delbuild(admin_id, payload):
    await delete_entry("builds", payload["search_key"])
    return f"✅ Билд «{payload['name']}» удалён."


@register_apply("delcounter")
async def _apply_delcounter(admin_id, payload):
    await delete_entry("counters", payload["search_key"])
    return f"✅ Контры «{payload['name']}» удалены."


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
    top_counters = await top_queries("counter", since_days=7)
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
    lines.append("\n🛡 Контры:")
    if top_counters:
        for i, (name, cnt) in enumerate(top_counters, 1):
            lines.append(f"{i}. {name} — {cnt}")
    else:
        lines.append("нет данных")
    await reply(message, "\n".join(lines))


# ---------- админ: режим подписки ----------

def _subtoggle_prompt(enable: bool):
    label = "✅ Включить платный режим" if enable else "✅ Выключить платный режим"
    question = (
        "Включить платный режим? Пробный день выдаётся только тем, кто им ещё не пользовался."
        if enable
        else "Выключить платный режим? Доступ у всех станет бесплатным."
    )
    return label, question


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
    label, question = _subtoggle_prompt(enable)
    await reply(message, question, reply_markup=action_confirm_kb(label, "❌ Отменить"))


@register_apply("subtoggle")
async def _apply_subtoggle(admin_id, payload):
    await set_setting("subscription_enabled", payload["enabled"])
    if payload["enabled"] == "1":
        return "✅ Платный режим включён."
    return "✅ Платный режим выключен — доступ у всех бесплатный."


# ---------- админ: режим техработ ----------

def _maint_prompt(enable: bool):
    label = "🛠 Начать техработы" if enable else "✅ Завершить техработы"
    question = (
        "Включить режим техработ? Пока он включён, время подписки у пользователей не будет расходоваться."
        if enable
        else "Завершить техработы? Всем пользователям продлим подписку на время, пока шли техработы."
    )
    return label, question


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
    label, question = _maint_prompt(enable)
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

ADDPROMO_PROMPT = (
    "Пришли 5 строк:\n"
    "КОД\n"
    "тип (percent или days)\n"
    "значение (число: для percent — процент скидки, для days — дней бесплатно)\n"
    "лимит использований\n"
    "период \"остывания\" в днях (после использования этого кода — сколько дней нельзя "
    "использовать другой промокод)\n\n"
    "Например:\nSALE20\npercent\n20\n100\n0"
)


@dp.message(Command("addpromo"), _is_admin_filter)
async def cmd_addpromo(message: Message):
    await set_pending_input(message.from_user.id, "args:addpromo")
    await reply(message, ADDPROMO_PROMPT)


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


# ---------- админ: резервная копия базы данных ----------

DB_IMPORT_TMP_PATH = DATABASE_PATH + ".import_tmp"


@dp.message(Command("exportdb"), _is_admin_filter)
async def cmd_exportdb(message: Message):
    from aiogram.types import FSInputFile

    if not os.path.exists(DATABASE_PATH):
        await reply(message, "Файл базы данных не найден.")
        return
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    await message.answer_document(
        FSInputFile(DATABASE_PATH, filename=f"backup_{ts}.db"),
        caption="📦 Полная резервная копия базы данных (SQLite).\n"
        "Содержит всех пользователей, карты, билды, тексты, кнопки, промокоды и настройки.",
    )


@dp.message(Command("importdb"), _is_admin_filter)
async def cmd_importdb(message: Message):
    if not message.document:
        await reply(
            message,
            "Пришли файл базы (.db), полученный через /exportdb, как документ с подписью "
            "/importdb.\n\n⚠️ Это полностью заменит текущую базу данных бота — отменить действие "
            "будет нельзя.",
        )
        return

    header = None
    try:
        await bot.download(message.document, destination=DB_IMPORT_TMP_PATH)
        with open(DB_IMPORT_TMP_PATH, "rb") as f:
            header = f.read(16)
    except Exception:
        header = None

    if header != b"SQLite format 3\x00":
        if os.path.exists(DB_IMPORT_TMP_PATH):
            os.remove(DB_IMPORT_TMP_PATH)
        await reply(
            message,
            "❌ Это не похоже на файл базы SQLite. Пришли файл, полученный через /exportdb.",
        )
        return

    await set_pending_action(message.from_user.id, "importdb", {"tmp_path": DB_IMPORT_TMP_PATH})
    await reply(
        message,
        "⚠️ Заменить текущую базу данных содержимым присланного файла?\n"
        "Все текущие пользователи, карты, билды, тексты, кнопки и настройки будут заменены "
        "данными из бэкапа. Это действие необратимо.",
        reply_markup=action_confirm_kb("✅ Заменить", "❌ Отменить"),
    )


@register_apply("importdb")
async def _apply_importdb(admin_id, payload):
    tmp_path = payload["tmp_path"]
    if not os.path.exists(tmp_path):
        return "❌ Временный файл бэкапа не найден, попробуй снова через /importdb."
    os.replace(tmp_path, DATABASE_PATH)
    await init_db()
    return "✅ База данных восстановлена из присланного файла."


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


# ---------- админ: inline-панель ----------

TEXT_DISPLAY_NAMES = {
    "welcome_text": "👋 Приветствие (/start)",
    "help_text": "ℹ️ Как пользоваться",
    "no_access_text": "🔒 Нет подписки",
    "search_prompt_text": "🔍 Подсказка поиска",
    "not_found_text": "🤔 Не найдено",
    "sub_active_text": "💎 Статус подписки",
    "reminder_text": "⏰ Напоминание",
    "payment_success_text": "✅ Успешная оплата",
    "referral_text": "👥 Мои рефералы",
    "purchase_confirm_text": "💳 Подтверждение покупки",
}

BUTTON_DISPLAY_NAMES = {
    "btn_search": "🔍 Поиск",
    "btn_subscription": "💎 Подписка",
    "btn_referrals": "👥 Рефералы",
    "btn_help": "ℹ️ Помощь",
    "btn_promo": "🎟 Промокод",
    "btn_find_maps": "🗺 Найти карту",
    "btn_find_builds": "⚔️ Найти билд",
    "btn_find_counters": "🛡 Найти контру",
}

ENTRY_TABLES = {
    "maps": {"title": "🗺 Карты", "icon": "🗺", "empty": "Пока нет ни одной карты.", "add_cmd": "/addmap"},
    "builds": {"title": "⚔️ Билды", "icon": "⚔️", "empty": "Пока нет ни одного билда.", "add_cmd": "/addbuild"},
    "counters": {
        "title": "🛡 Контры",
        "icon": "🛡",
        "empty": "Пока нет ни одной записи с контрами.",
        "add_cmd": "/addcounter",
    },
}

ENTRY_DELETE_ACTIONS = {
    "maps": ("delmap", "карту"),
    "builds": ("delbuild", "билд"),
    "counters": ("delcounter", "контры"),
}


def panel_main_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📝 Тексты", callback_data="panel:texts", style="success")
    kb.button(text="🔘 Кнопки", callback_data="panel:buttons", style="success")
    kb.button(text="🗺 Карты", callback_data="panel:list:maps:0", style="success")
    kb.button(text="⚔️ Билды", callback_data="panel:list:builds:0", style="success")
    kb.button(text="🛡 Контры", callback_data="panel:list:counters:0", style="success")
    kb.button(text="👤 Пользователи", callback_data="panel:userspage:0", style="success")
    kb.button(text="🎟 Промокоды", callback_data="panel:promos", style="success")
    kb.button(text="⚙️ Настройки", callback_data="panel:settings", style="success")
    kb.button(text="👮 Админы", callback_data="panel:admins", style="success")
    kb.adjust(2)
    return kb.as_markup()


async def _panel_main_screen():
    return "🛠 Админ-панель. Выбери раздел:", panel_main_kb()


@dp.message(Command("panel"), _is_admin_filter)
async def cmd_panel(message: Message):
    text, markup = await _panel_main_screen()
    await reply(message, text, reply_markup=markup)


@dp.callback_query(F.data == "panel:main", _is_admin_cb_filter)
async def cb_panel_main(callback: CallbackQuery):
    text, markup = await _panel_main_screen()
    await reply(callback, text, reply_markup=markup)
    await callback.answer()


# ---- тексты ----

def panel_texts_kb():
    kb = InlineKeyboardBuilder()
    for key in EDITABLE_TEXTS:
        kb.button(text=TEXT_DISPLAY_NAMES.get(key, key), callback_data=f"panel:text:{key}", style="success")
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="◀️ В меню", callback_data="panel:main", style="primary"))
    return kb.as_markup()


def panel_text_view_kb(key):
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Изменить", callback_data=f"panel:textedit:{key}", style="success")
    kb.button(text="◀️ К списку", callback_data="panel:texts", style="primary")
    kb.adjust(1)
    return kb.as_markup()


@dp.callback_query(F.data == "panel:texts", _is_admin_cb_filter)
async def cb_panel_texts(callback: CallbackQuery):
    await reply(callback, "📝 Редактируемые тексты — выбери, что изменить:", reply_markup=panel_texts_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:text:"), _is_admin_cb_filter)
async def cb_panel_text_view(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    if key not in EDITABLE_TEXTS:
        await callback.answer()
        return
    value = await get_setting(key)
    photo = await get_setting_photo(key)
    name = TEXT_DISPLAY_NAMES.get(key, key)
    await reply(
        callback, f"{name}:\n\n{value}", photo_file_id=photo, reply_markup=panel_text_view_kb(key)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:textedit:"), _is_admin_cb_filter)
async def cb_panel_text_edit(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    if key not in EDITABLE_TEXTS:
        await callback.answer()
        return
    _cmd, prompt, _default = EDITABLE_TEXTS[key]
    await set_pending_input(callback.from_user.id, f"settext:{key}")
    await reply(callback, prompt)
    await callback.answer()


# ---- кнопки меню ----

def panel_buttons_kb():
    kb = InlineKeyboardBuilder()
    for key in BUTTON_LABELS:
        kb.button(text=BUTTON_DISPLAY_NAMES.get(key, key), callback_data=f"panel:btn:{key}", style="success")
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="◀️ В меню", callback_data="panel:main", style="primary"))
    return kb.as_markup()


def panel_btn_view_kb(key):
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Изменить", callback_data=f"panel:btnedit:{key}", style="success")
    kb.button(text="◀️ К списку", callback_data="panel:buttons", style="primary")
    kb.adjust(1)
    return kb.as_markup()


@dp.callback_query(F.data == "panel:buttons", _is_admin_cb_filter)
async def cb_panel_buttons(callback: CallbackQuery):
    await reply(
        callback, "🔘 Названия кнопок меню — выбери, что изменить:", reply_markup=panel_buttons_kb()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:btn:"), _is_admin_cb_filter)
async def cb_panel_btn_view(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    if key not in BUTTON_LABELS:
        await callback.answer()
        return
    value = await get_setting(key)
    name = BUTTON_DISPLAY_NAMES.get(key, key)
    await reply(
        callback, f"Текущее название кнопки «{name}»:\n\n{value}", reply_markup=panel_btn_view_kb(key)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:btnedit:"), _is_admin_cb_filter)
async def cb_panel_btn_edit(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    if key not in BUTTON_LABELS:
        await callback.answer()
        return
    _cmd, prompt, _default = BUTTON_LABELS[key]
    await set_pending_input(callback.from_user.id, f"setbtn:{key}")
    await reply(callback, prompt)
    await callback.answer()


# ---- карты / билды ----

async def _panel_entry_list_screen(table: str, offset: int):
    names = await list_entry_names(table)
    total = len(names)
    info = ENTRY_TABLES[table]
    page = names[offset: offset + PANEL_ENTRY_PAGE_SIZE]

    kb = InlineKeyboardBuilder()
    for i, name in enumerate(page):
        abs_idx = offset + i
        label = name if len(name) <= 40 else name[:37] + "…"
        kb.button(text=label, callback_data=f"panel:item:{table}:{abs_idx}", style="success")
    kb.adjust(1)

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(
            text="◀️", callback_data=f"panel:list:{table}:{max(0, offset - PANEL_ENTRY_PAGE_SIZE)}",
            style="success",
        ))
    if offset + PANEL_ENTRY_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="▶️", callback_data=f"panel:list:{table}:{offset + PANEL_ENTRY_PAGE_SIZE}",
            style="success",
        ))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text="◀️ В меню", callback_data="panel:main", style="primary"))

    if not total:
        text = f"{info['empty']} Добавить можно командой {info['add_cmd']}."
    else:
        text = f"{info['title']} — всего {total}.\nВыбери запись, чтобы посмотреть или удалить."
    return text, kb.as_markup()


@dp.callback_query(F.data.startswith("panel:list:"), _is_admin_cb_filter)
async def cb_panel_list(callback: CallbackQuery):
    _, _, table, offset_s = callback.data.split(":")
    text, markup = await _panel_entry_list_screen(table, int(offset_s))
    await reply(callback, text, reply_markup=markup)
    await callback.answer()


def panel_item_view_kb(table, idx):
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Удалить", callback_data=f"panel:itemdel:{table}:{idx}", style="danger")
    kb.button(text="◀️ К списку", callback_data=f"panel:list:{table}:0", style="primary")
    kb.adjust(1)
    return kb.as_markup()


@dp.callback_query(F.data.startswith("panel:item:"), _is_admin_cb_filter)
async def cb_panel_item(callback: CallbackQuery):
    _, _, table, idx_s = callback.data.split(":")
    idx = int(idx_s)
    names = await list_entry_names(table)
    if idx >= len(names):
        await callback.answer("Запись больше не существует", show_alert=True)
        return
    entry = await find_entry(table, names[idx])
    if not entry:
        await callback.answer("Запись больше не существует", show_alert=True)
        return
    icon = ENTRY_TABLES[table]["icon"]
    await reply(
        callback,
        f"{icon} <b>{entry['name']}</b>\n\n{entry['content']}",
        photo_file_id=entry["photo_file_id"],
        reply_markup=panel_item_view_kb(table, idx),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:itemdel:"), _is_admin_cb_filter)
async def cb_panel_itemdel(callback: CallbackQuery):
    _, _, table, idx_s = callback.data.split(":")
    idx = int(idx_s)
    names = await list_entry_names(table)
    if idx >= len(names):
        await callback.answer("Запись больше не существует", show_alert=True)
        return
    entry = await find_entry(table, names[idx])
    if not entry:
        await callback.answer("Запись больше не существует", show_alert=True)
        return
    action_type, label = ENTRY_DELETE_ACTIONS[table]
    await set_pending_action(
        callback.from_user.id, action_type, {"search_key": entry["search_key"], "name": entry["name"]}
    )
    await reply(
        callback,
        f"🗑 Удалить эту запись — {label} «{entry['name']}»?\n\n{entry['content']}",
        photo_file_id=entry["photo_file_id"],
        reply_markup=action_confirm_kb("🗑 Удалить", "↩️ Оставить"),
    )
    await callback.answer()


# ---- пользователи ----

def panel_users_list_kb(users, offset, total):
    kb = InlineKeyboardBuilder()
    for u in users:
        label = f"👤 {u['user_id']}" + (f" @{u['username']}" if u["username"] else "")
        kb.button(text=label, callback_data=f"panel:user:{u['user_id']}", style="success")
    kb.adjust(1)

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(
            text="◀️", callback_data=f"panel:userspage:{max(0, offset - PANEL_USERS_PAGE_SIZE)}",
            style="success",
        ))
    if offset + PANEL_USERS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="▶️", callback_data=f"panel:userspage:{offset + PANEL_USERS_PAGE_SIZE}",
            style="success",
        ))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text="◀️ В меню", callback_data="panel:main", style="primary"))
    return kb.as_markup()


@dp.callback_query(F.data.startswith("panel:userspage:"), _is_admin_cb_filter)
async def cb_panel_userspage(callback: CallbackQuery):
    offset = int(callback.data.split(":")[2])
    total = await count_users()
    users = await list_users(offset, PANEL_USERS_PAGE_SIZE)
    text = (
        f"👤 Пользователи — всего {total}.\nВыбери, чтобы посмотреть подробнее."
        if users
        else "Пока нет пользователей."
    )
    await reply(callback, text, reply_markup=panel_users_list_kb(users, offset, total))
    await callback.answer()


def panel_user_card_kb(user_id, has_sub):
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Выдать 30 дней", callback_data=f"panel:usersub:{user_id}:30", style="success")
    if has_sub:
        kb.button(text="🗑 Снять подписку", callback_data=f"panel:userdelsub:{user_id}", style="danger")
    kb.button(text="◀️ К списку", callback_data="panel:userspage:0", style="primary")
    kb.adjust(1)
    return kb.as_markup()


@dp.callback_query(F.data.startswith("panel:user:"), _is_admin_cb_filter)
async def cb_panel_user_card(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[2])
    user = await get_user(user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await reply(
        callback, _fmt_user_card(user), reply_markup=panel_user_card_kb(user_id, bool(user["sub_until"]))
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:usersub:"), _is_admin_cb_filter)
async def cb_panel_usersub(callback: CallbackQuery):
    _, _, user_id_s, days_s = callback.data.split(":")
    await _process_addsub(callback, int(user_id_s), int(days_s))
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:userdelsub:"), _is_admin_cb_filter)
async def cb_panel_userdelsub(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[2])
    await _process_delsub(callback, user_id)
    await callback.answer()


# ---- промокоды ----

async def _panel_promos_screen():
    promos = await list_promos()
    kb = InlineKeyboardBuilder()
    for i, p in enumerate(promos):
        type_label = "%" if p["type"] == "percent" else "дн."
        label = f"{p['code']} ({p['value']}{type_label}, {p['uses_count']}/{p['uses_limit']})"
        kb.button(text=label[:60], callback_data=f"panel:promoitem:{i}", style="success")
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="➕ Добавить промокод", callback_data="panel:promoadd", style="success"))
    kb.row(InlineKeyboardButton(text="◀️ В меню", callback_data="panel:main", style="primary"))
    text = "🎟 Промокоды — выбери, чтобы посмотреть или удалить:" if promos else "Промокодов пока нет."
    return text, kb.as_markup()


@dp.callback_query(F.data == "panel:promos", _is_admin_cb_filter)
async def cb_panel_promos(callback: CallbackQuery):
    text, markup = await _panel_promos_screen()
    await reply(callback, text, reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "panel:promoadd", _is_admin_cb_filter)
async def cb_panel_promoadd(callback: CallbackQuery):
    await set_pending_input(callback.from_user.id, "args:addpromo")
    await reply(callback, ADDPROMO_PROMPT)
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:promoitem:"), _is_admin_cb_filter)
async def cb_panel_promoitem(callback: CallbackQuery):
    idx = int(callback.data.split(":")[2])
    promos = await list_promos()
    if idx >= len(promos):
        await callback.answer("Промокод больше не существует", show_alert=True)
        return
    p = promos[idx]
    type_label = "скидка %" if p["type"] == "percent" else "дней бесплатно"
    text = (
        f"🎟 {p['code']}\nТип: {type_label}\nЗначение: {p['value']}\n"
        f"Использован: {p['uses_count']}/{p['uses_limit']}\nОстывание: {p['cooldown_days']} дн."
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Удалить", callback_data=f"panel:promodel:{idx}", style="danger")
    kb.button(text="◀️ К списку", callback_data="panel:promos", style="primary")
    kb.adjust(1)
    await reply(callback, text, reply_markup=kb.as_markup())
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:promodel:"), _is_admin_cb_filter)
async def cb_panel_promodel(callback: CallbackQuery):
    idx = int(callback.data.split(":")[2])
    promos = await list_promos()
    if idx >= len(promos):
        await callback.answer("Промокод больше не существует", show_alert=True)
        return
    code = promos[idx]["code"]
    await set_pending_action(callback.from_user.id, "delpromo", {"code": code})
    await reply(
        callback, f"Удалить промокод «{code}»?", reply_markup=action_confirm_kb("🗑 Удалить", "↩️ Оставить")
    )
    await callback.answer()


# ---- настройки ----

async def _panel_settings_screen():
    sub_on = await is_subscription_enabled()
    maint_on = await is_maintenance_mode()
    text = (
        "⚙️ Настройки\n\n"
        f"Платный режим: {'включён' if sub_on else 'выключен (бесплатно всем)'}\n"
        f"Техработы: {'идут' if maint_on else 'не идут'}"
    )
    kb = InlineKeyboardBuilder()
    kb.button(
        text=("🔴 Выключить платный режим" if sub_on else "🟢 Включить платный режим"),
        callback_data=f"panel:subtoggle:{0 if sub_on else 1}",
        style="success",
    )
    kb.button(
        text=("✅ Завершить техработы" if maint_on else "🛠 Начать техработы"),
        callback_data=f"panel:maint:{0 if maint_on else 1}",
        style="success",
    )
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="◀️ В меню", callback_data="panel:main", style="primary"))
    return text, kb.as_markup()


@dp.callback_query(F.data == "panel:settings", _is_admin_cb_filter)
async def cb_panel_settings(callback: CallbackQuery):
    text, markup = await _panel_settings_screen()
    await reply(callback, text, reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:subtoggle:"), _is_admin_cb_filter)
async def cb_panel_subtoggle(callback: CallbackQuery):
    enable = callback.data.split(":")[2] == "1"
    await set_pending_action(callback.from_user.id, "subtoggle", {"enabled": "1" if enable else "0"})
    label, question = _subtoggle_prompt(enable)
    await reply(callback, question, reply_markup=action_confirm_kb(label, "❌ Отменить"))
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:maint:"), _is_admin_cb_filter)
async def cb_panel_maint(callback: CallbackQuery):
    enable = callback.data.split(":")[2] == "1"
    if enable and await is_maintenance_mode():
        await callback.answer("Техработы уже идут", show_alert=True)
        return
    if not enable and not await is_maintenance_mode():
        await callback.answer("Техработы и так не идут", show_alert=True)
        return
    await set_pending_action(callback.from_user.id, "maintenance_toggle", {"enabled": enable})
    label, question = _maint_prompt(enable)
    await reply(callback, question, reply_markup=action_confirm_kb(label, "❌ Отменить"))
    await callback.answer()


# ---- админы ----

def panel_admins_kb(admin_ids):
    kb = InlineKeyboardBuilder()
    for aid in admin_ids:
        if aid == MAIN_ADMIN_ID:
            continue
        kb.button(text=f"🗑 {aid}", callback_data=f"panel:admindel:{aid}", style="danger")
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="➕ Добавить админа", callback_data="panel:adminadd", style="success"))
    kb.row(InlineKeyboardButton(text="◀️ В меню", callback_data="panel:main", style="primary"))
    return kb.as_markup()


@dp.callback_query(F.data == "panel:admins", _is_admin_cb_filter)
async def cb_panel_admins(callback: CallbackQuery):
    admin_ids = sorted(await get_all_admin_ids())
    lines = ["👮 Админы:\n"]
    for aid in admin_ids:
        tag = " (главный)" if aid == MAIN_ADMIN_ID else ""
        lines.append(f"{aid}{tag}")
    await reply(callback, "\n".join(lines), reply_markup=panel_admins_kb(admin_ids))
    await callback.answer()


@dp.callback_query(F.data == "panel:adminadd", _is_admin_cb_filter)
async def cb_panel_adminadd(callback: CallbackQuery):
    await set_pending_input(callback.from_user.id, "args:addadmin")
    await reply(callback, "Пришли user_id человека, которого нужно сделать админом.")
    await callback.answer()


@dp.callback_query(F.data.startswith("panel:admindel:"), _is_admin_cb_filter)
async def cb_panel_admindel(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[2])
    await _process_deladmin(callback, user_id)
    await callback.answer()


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
