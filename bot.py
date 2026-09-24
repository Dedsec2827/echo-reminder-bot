"""
bot.py
------
Базовий Telegram-бот на Aiogram 3.x для Echo.
"""

import asyncio
import logging
import os
import random
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup,
    InlineKeyboardButton, WebAppInfo,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

import database as db

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("echo-bot")
router = Router()

# Fallback and default language is always English.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "welcome": "Hi! Echo helps you not forget what matters.\n\nTap the button below to open your reminders.",
        "app": "Your reminders are here:",
        "open_echo": "Open Echo",
    },
    "uk": {
        "welcome": "Привіт! Echo допоможе не забувати важливе.\n\nТисни кнопку нижче, щоб відкрити список нагадувань.",
        "app": "Твої нагадування тут:",
        "open_echo": "Відкрити Echo",
    },
}
LANGUAGE_PROMPT = "Please choose your language / Будь ласка, оберіть мову:"
LANGUAGE_PICKER_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en"),
    InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang:uk"),
]])


def t(lang: str, key: str) -> str:
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, TRANSLATIONS["en"][key])


def _webapp_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t(lang, "open_echo"), web_app=WebAppInfo(url=WEBAPP_URL))]])


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)
    await db.upsert_chat(chat_id=user.id, owner_id=user.id, title="Personal messages", chat_type="private")
    await message.answer(LANGUAGE_PROMPT, reply_markup=LANGUAGE_PICKER_KEYBOARD)


@router.callback_query(F.data.startswith("lang:"))
async def on_language_pick(callback: CallbackQuery) -> None:
    lang = callback.data.split(":", 1)[1]
    if lang not in TRANSLATIONS:
        lang = "en"
    await db.set_user_language(callback.from_user.id, lang)
    await callback.answer()
    try:
        await callback.message.edit_text(t(lang, "welcome"), reply_markup=_webapp_keyboard(lang))
    except Exception:
        pass

@router.message(Command("app"))
async def cmd_app(message: Message) -> None:
    lang = await db.get_user_language(message.from_user.id)
    await message.answer(t(lang, "app"), reply_markup=_webapp_keyboard(lang))

@router.my_chat_member()
async def on_bot_added_to_chat(event: ChatMemberUpdated) -> None:
    new_status = event.new_chat_member.status
    if new_status not in ("member", "administrator") or event.chat.type == ChatType.PRIVATE: return
    owner_id = event.from_user.id
    await db.upsert_user(owner_id, event.from_user.username, event.from_user.first_name)
    await db.upsert_chat(chat_id=event.chat.id, owner_id=owner_id, title=event.chat.title or "Untitled chat", chat_type=event.chat.type)
    logger.info("Chat %s saved as route for user %s", event.chat.id, owner_id)

def _pick_message(text: str, use_message_pool: bool) -> str:
    if not use_message_pool: return text
    variants = [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]
    return random.choice(variants) if variants else text

def _reminder_keyboard(reminder: dict) -> Optional[InlineKeyboardMarkup]:
    buttons: list[InlineKeyboardButton] = []
    if reminder["snooze_enabled"]: buttons.append(InlineKeyboardButton(text="Відкласти", callback_data=f"snooze:{reminder['id']}"))
    if reminder["tracker_enabled"]: buttons.append(InlineKeyboardButton(text="Виконано", callback_data=f"done:{reminder['id']}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None

SNOOZE_OPTIONS: dict[str, int] = {"15m": 15, "1h": 60, "1d": 24 * 60}
SNOOZE_OPTION_LABELS: dict[str, str] = {"15m": "На 15 хв", "1h": "На 1 годину", "1d": "На завтра"}

def _snooze_picker_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=label, callback_data=f"snooze_pick:{reminder_id}:{key}") for key, label in SNOOZE_OPTION_LABELS.items()]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

async def _send_message_with_photo(bot: Bot, chat_id: int, text: str, media_url: Optional[str], keyboard: Optional[InlineKeyboardMarkup]) -> None:
    if media_url:
        await bot.send_photo(chat_id=chat_id, photo=media_url, caption=text, reply_markup=keyboard, disable_notification=False)
    else:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, disable_notification=False)


async def _delete_channel_media(bot: Bot, reminder: dict) -> None:
    """Best-effort removal of a reminder's stored photo from the storage channel; never raises.
    Mirrors api.py's cleanup, needed here too since a reminder can be deleted directly
    (auto-delete after sending, or the "Done" button) without going through the API."""
    message_id = reminder.get("media_message_id")
    if not message_id or not CHANNEL_ID:
        return
    try:
        await bot.delete_message(chat_id=int(CHANNEL_ID), message_id=message_id)
    except Exception:
        logger.warning("Couldn't delete channel message %s", message_id, exc_info=True)

async def send_test_message(bot: Bot, chat_id: int, text: str, media_url: Optional[str] = None, use_message_pool: bool = False, snooze_enabled: bool = False, tracker_enabled: bool = False) -> None:
    message_text = _pick_message(text, use_message_pool)
    keyboard = _reminder_keyboard({"id": 0, "snooze_enabled": snooze_enabled, "tracker_enabled": tracker_enabled})
    await _send_message_with_photo(bot, chat_id, message_text, media_url, keyboard)

@router.callback_query(F.data.startswith("snooze:"))
async def on_snooze(callback: CallbackQuery) -> None:
    reminder_id = int(callback.data.split(":", 1)[1])
    reminder = await db.get_reminder(reminder_id)
    if not reminder: return await callback.answer("Це тестове повідомлення, функція недоступна", show_alert=True)
    await callback.answer()
    try: await callback.message.edit_reply_markup(reply_markup=_snooze_picker_keyboard(reminder_id))
    except Exception: pass

@router.callback_query(F.data.startswith("snooze_pick:"))
async def on_snooze_pick(callback: CallbackQuery) -> None:
    _, reminder_id_str, option_key = callback.data.split(":", 2)
    reminder_id = int(reminder_id_str)
    reminder = await db.get_reminder(reminder_id)
    if not reminder: return await callback.answer("Це тестове повідомлення", show_alert=True)
    minutes = SNOOZE_OPTIONS.get(option_key)
    if minutes is None: return await callback.answer()
    new_run_date = await db.snooze_reminder(reminder_id, minutes=minutes)
    await callback.answer(f"Відкладено: {SNOOZE_OPTION_LABELS[option_key]}")
    try: await callback.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    logger.info("Reminder %s snoozed until %s", reminder_id, new_run_date)

@router.callback_query(F.data.startswith("done:"))
async def on_done(callback: CallbackQuery) -> None:
    reminder_id = int(callback.data.split(":", 1)[1])
    reminder = await db.get_reminder(reminder_id)
    if not reminder: return await callback.answer("Це тестове повідомлення, функція недоступна", show_alert=True)
    is_one_time = reminder.get("recurrence", "none") == "none"
    streak = await db.increment_streak(reminder_id, deactivate=False)
    await callback.answer(f"Готово! Стрік: {streak} 🔥")
    try: await callback.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    if is_one_time:
        await db.delete_reminder(reminder_id)
        await _delete_channel_media(callback.bot, reminder)

def _recurring_reminder_expired(reminder: dict, reference: datetime) -> bool:
    end_date = reminder.get("end_date")
    if not end_date: return False
    return reference >= db.parse_iso_utc(end_date)

async def _advance_or_expire_recurring(reminder: dict) -> None:
    recurrence = reminder["recurrence"]
    if _recurring_reminder_expired(reminder, db.utcnow()):
        await db.mark_reminder_sent(reminder["id"], keep_active=False)
        logger.info("Reminder %s reached its end date", reminder["id"])
        return
    next_run_date = db.compute_next_run_date(reminder["run_date"], recurrence, daily_times=reminder.get("daily_times"))
    await db.reschedule_recurring_reminder(reminder["id"], next_run_date)
    logger.info("Reminder %s is recurring (%s); rescheduled to %s", reminder["id"], recurrence, next_run_date)

async def send_reminder(bot: Bot, reminder: dict) -> None:
    keyboard = _reminder_keyboard(reminder)
    message_text = _pick_message(reminder["text"], bool(reminder.get("use_message_pool")))
    media_url = reminder.get("media_url")
    try:
        await _send_message_with_photo(bot, reminder["chat_id"], message_text, media_url, keyboard)
    except Exception:
        logger.exception("Failed to send reminder %s", reminder["id"])
        if reminder.get("recurrence", "none") in db.RECURRING_TYPES: await _advance_or_expire_recurring(reminder)
        else: await db.mark_reminder_sent(reminder["id"], keep_active=False)
        return

    recurrence = reminder.get("recurrence", "none")
    try:
        if recurrence in db.RECURRING_TYPES:
            await _advance_or_expire_recurring(reminder)
        elif keyboard:
            await db.mark_reminder_sent(reminder["id"], keep_active=True)
        else:
            await db.delete_reminder(reminder["id"])
            await _delete_channel_media(bot, reminder)
    except Exception:
        logger.exception("Failed to finalize reminder %s after sending", reminder["id"])
        await db.mark_reminder_sent(reminder["id"], keep_active=False)

async def check_due_reminders(bot: Bot) -> None:
    due = await db.get_due_reminders()
    for reminder in due:
        try:
            await send_reminder(bot, reminder)
        except Exception:
            # send_reminder already guards its own internals; this is a last-resort net so one
            # bad reminder can't stop the rest of the batch from going out on this tick.
            logger.exception("Unexpected error handling reminder %s", reminder.get("id"))

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(check_due_reminders, "interval", seconds=30, args=[bot], id="due_reminders_check")
    scheduler.start()
    return scheduler

def create_bot_and_dispatcher() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    return bot, dp

async def main() -> None:
    await db.init_db()
    bot, dp = create_bot_and_dispatcher()
    setup_scheduler(bot)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())