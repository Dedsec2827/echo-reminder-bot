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
        "welcome": (
            "This is Echo, a reminder and scheduling bot.\n\n"
            "Functions:\n"
            "— Scheduling one-off or recurring reminders (daily, weekly, yearly), including multiple "
            "times per day.\n"
            "— Administration of groups and channels: add the bot as an administrator to schedule posts "
            "for a chat or channel.\n"
            "— Message pool: a set of message variants from which one is selected at random on each send.\n"
            "— Tracker and snooze controls attached to a reminder message.\n\n"
            "Use the button below to open the application and create a reminder."
        ),
        "help": (
            "<b>Echo — function reference</b>\n\n"
            "Open the application with the button below to create a reminder: one-off or recurring "
            "(daily, weekly, yearly). One or more times may be specified.\n\n"
            "To post to a group or channel, add the bot there as an administrator. The chat will then "
            "appear as a destination in the application.\n\n"
            "<b>Message pool</b>: enter several message variants separated by a blank line. One variant "
            "is selected at random for each send.\n\n"
            "<b>Snooze</b> and <b>Tracker</b> may be enabled per reminder. Snooze postpones a reminder by "
            "a selected interval. Tracker marks a reminder as completed and records a streak count.\n\n"
            "To reopen the application, send /app."
        ),
        "app": "Your reminders:",
        "open_echo": "Open Echo",
    },
    "uk": {
        "welcome": (
            "Це Echo — бот для нагадувань і планування публікацій.\n\n"
            "Функції:\n"
            "— Планування одноразових або повторюваних нагадувань (щодня, щотижня, щороку), "
            "зокрема з кількома часами на день.\n"
            "— Адміністрування груп і каналів: додайте бота адміністратором, щоб планувати публікації "
            "в чаті чи каналі.\n"
            "— Пул повідомлень: набір варіантів тексту, з якого при кожній відправці обирається один "
            "випадковий.\n"
            "— Кнопки трекера та відкладення на повідомленні нагадування.\n\n"
            "Натисніть кнопку нижче, щоб відкрити застосунок і створити нагадування."
        ),
        "help": (
            "<b>Echo — довідка щодо функцій</b>\n\n"
            "Відкрийте застосунок кнопкою нижче, щоб створити нагадування: одноразове або повторюване "
            "(щодня, щотижня, щороку). Можна вказати один або кілька часів.\n\n"
            "Щоб публікувати в групі чи каналі, додайте бота туди адміністратором. Чат з'явиться "
            "як місце призначення в застосунку.\n\n"
            "<b>Пул повідомлень</b>: введіть кілька варіантів тексту, розділених порожнім рядком. "
            "При кожній відправці обирається один випадковий варіант.\n\n"
            "<b>Відкладення</b> та <b>Трекер</b> можна увімкнути для окремого нагадування. Відкладення "
            "переносить нагадування на обраний інтервал. Трекер позначає нагадування виконаним і "
            "веде облік серії виконань.\n\n"
            "Щоб повторно відкрити застосунок, надішліть /app."
        ),
        "app": "Ваші нагадування:",
        "open_echo": "Відкрити Echo",
    },
    "hy": {
        "welcome": (
            "Սա Echo-ն է՝ հիշեցումների և հրապարակումների պլանավորման բոտ։\n\n"
            "Գործառույթներ.\n"
            "— Մեկանգամյա կամ կրկնվող հիշեցումների պլանավորում (ամեն օր, շաբաթ, տարի), "
            "այդ թվում՝ օրական մի քանի ժամով։\n"
            "— Խմբերի և ալիքների կառավարում. ավելացրեք բոտը որպես ադմինիստրատոր՝ չատում կամ "
            "ալիքում հրապարակումներ պլանավորելու համար։\n"
            "— Հաղորդագրությունների խումբ. տեքստի տարբերակների ցանկ, որից յուրաքանչյուր ուղարկման "
            "ժամանակ պատահականորեն ընտրվում է մեկը։\n"
            "— Հետևող և հետաձգման կոճակներ հիշեցման հաղորդագրության վրա։\n\n"
            "Օգտագործեք ներքևի կոճակը՝ հավելվածը բացելու և հիշեցում ստեղծելու համար։"
        ),
        "help": (
            "<b>Echo — գործառույթների նկարագիր</b>\n\n"
            "Բացեք հավելվածը ներքևի կոճակով՝ հիշեցում ստեղծելու համար. մեկանգամյա կամ կրկնվող "
            "(ամեն օր, շաբաթ, տարի)։ Կարելի է նշել մեկ կամ մի քանի ժամ։\n\n"
            "Խմբում կամ ալիքում հրապարակելու համար ավելացրեք բոտը այնտեղ որպես ադմինիստրատոր։ "
            "Չատը հայտնվելու է հավելվածում որպես ուղղություն։\n\n"
            "<b>Հաղորդագրությունների խումբ</b>. մուտքագրեք մի քանի տեքստային տարբերակ՝ բաժանված "
            "դատարկ տողով։ Յուրաքանչյուր ուղարկման ժամանակ պատահականորեն ընտրվում է մեկը։\n\n"
            "<b>Հետաձգում</b> և <b>Հետևող</b> գործառույթները կարող են միացվել առանձին հիշեցման համար։ "
            "Հետաձգումը հիշեցումը տեղափոխում է ընտրված ժամանակահատվածով։ Հետևողը նշում է հիշեցումը "
            "կատարված և հաշվում է հաջորդական կատարումների քանակը։\n\n"
            "Հավելվածը կրկին բացելու համար ուղարկեք /app։"
        ),
        "app": "Ձեր հիշեցումները՝",
        "open_echo": "Բացել Echo-ն",
    },
}
LANGUAGE_PROMPT = "Please choose your language / Будь ласка, оберіть мову / Խնդրում ենք ընտրել լեզուն:"
LANGUAGE_PICKER_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en"),
    InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang:uk"),
    InlineKeyboardButton(text="🇦🇲 Հայերեն", callback_data="lang:hy"),
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

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    lang = await db.get_user_language(message.from_user.id)
    await message.answer(t(lang, "help"), reply_markup=_webapp_keyboard(lang))

ACTIVE_CHAT_STATUSES = ("member", "administrator", "creator")
REMOVED_CHAT_STATUSES = ("left", "kicked")

@router.my_chat_member()
async def on_bot_added_to_chat(event: ChatMemberUpdated) -> None:
    if event.chat.type == ChatType.PRIVATE:
        return
    new_status = event.new_chat_member.status
    if new_status in ACTIVE_CHAT_STATUSES:
        # Fires for both a plain group membership and a channel/group admin promotion,
        # and again on re-promotion - upsert_chat is idempotent so this stays correct
        # however many times it fires.
        owner_id = event.from_user.id
        await db.upsert_user(owner_id, event.from_user.username, event.from_user.first_name)
        await db.upsert_chat(chat_id=event.chat.id, owner_id=owner_id, title=event.chat.title or "Untitled chat", chat_type=event.chat.type)
        logger.info("Chat %s saved as route for user %s", event.chat.id, owner_id)
    elif new_status in REMOVED_CHAT_STATUSES:
        # Bot removed/kicked - drop the stale destination so it stops showing up as a
        # route (this also cascades to delete any reminders still pointed at it).
        await db.delete_chat(event.chat.id)
        logger.info("Bot removed from chat %s; route deleted", event.chat.id)

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
        # A photo-only reminder has empty/None text - Telegram accepts caption=None fine,
        # it just sends the photo without a caption instead of erroring on an empty string.
        await bot.send_photo(chat_id=chat_id, photo=media_url, caption=text or None, reply_markup=keyboard, disable_notification=False)
    else:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, disable_notification=False)


async def _delete_channel_media(bot: Bot, reminder: dict) -> None:
    """Best-effort removal of a reminder's stored photo from the storage channel; never raises.
    Mirrors api.py's cleanup, needed here too since a reminder can be deleted directly
    (auto-delete after sending, or the "Done" button) without going through the API."""
    message_id = reminder.get("media_message_id")
    if not message_id or not CHANNEL_ID:
        return
    # A multi-time "Once" reminder can share one photo across several rows - skip deletion
    # while any sibling reminder still points at the same channel post.
    if await db.media_still_referenced(message_id, exclude_id=reminder.get("id")):
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

async def _advance_or_expire_recurring(bot: Bot, reminder: dict) -> None:
    recurrence = reminder["recurrence"]
    if _recurring_reminder_expired(reminder, db.utcnow()):
        await db.mark_reminder_sent(reminder["id"], keep_active=False)
        logger.info("Reminder %s reached its end date", reminder["id"])
        return
    next_run_date = db.compute_next_run_date(
        reminder["run_date"], recurrence, reminder.get("daily_times"), reminder.get("tz_offset_minutes", 0)
    )
    if next_run_date is None:
        # A "Once" reminder with multiple times has fired its last remaining slot.
        await db.delete_reminder(reminder["id"])
        await _delete_channel_media(bot, reminder)
        logger.info("Reminder %s (one-off, multi-time) exhausted all times; deleted", reminder["id"])
        return
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
        if reminder.get("recurrence", "none") in db.RECURRING_TYPES: await _advance_or_expire_recurring(bot, reminder)
        else: await db.mark_reminder_sent(reminder["id"], keep_active=False)
        return

    recurrence = reminder.get("recurrence", "none")
    try:
        if recurrence in db.RECURRING_TYPES or (recurrence == "none" and reminder.get("daily_times")):
            await _advance_or_expire_recurring(bot, reminder)
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