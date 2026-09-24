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
            "Hey, I'm Echo 👋 Think of me as a little personal assistant who remembers things so you don't have to.\n\n"
            "Tell me what to remind you about and when — just once, or on repeat every day, week or year — "
            "and I'll nudge you right on time. Got a group or a channel? Add me there as an admin and I can "
            "schedule posts for your community too.\n\n"
            "Want your habits to feel fresh? Turn on the Message Pool and I'll pick a random phrase from your list "
            "each time, so reminders never sound stale. You can also add Snooze and Tracker buttons to any reminder "
            "— I'll even keep count of your streak.\n\n"
            "Tap the button below to set up your first reminder."
        ),
        "help": (
            "<b>A quick tour of Echo</b>\n\n"
            "Open the app with the button below and create a reminder — one-off or repeating "
            "(daily, weekly or yearly). Pick a time, or a few, and I'll take it from there.\n\n"
            "Want me to post in a group or channel? Add me there as an admin and it will show up as a destination "
            "in the app, so you can schedule community posts ahead of time.\n\n"
            "To keep things varied, switch on the <b>Message Pool</b>: write a few phrases, separate them with "
            "a blank line, and I'll send a random one each time — perfect for habits you don't want to feel repetitive.\n\n"
            "And if you like a bit of accountability, add <b>Snooze</b> to push a reminder back for a while, "
            "or the <b>Tracker</b> to mark it done and build a streak.\n\n"
            "Lost the app? Just send /app."
        ),
        "app": "Your reminders are here:",
        "open_echo": "Open Echo",
    },
    "uk": {
        "welcome": (
            "Привіт, я Echo 👋 Вважай мене своїм маленьким персональним помічником, який пам'ятає замість тебе.\n\n"
            "Просто скажи, про що і коли нагадати — один раз або з повтором щодня, щотижня чи щороку — "
            "і я обов'язково штовхну тебе вчасно. Маєш групу чи канал? Додай мене туди адміном, і я зможу "
            "планувати публікації для твоєї спільноти.\n\n"
            "Хочеш, щоб звички не набридали? Увімкни Пул повідомлень — щоразу я обиратиму випадкову фразу зі "
            "списку, тож нагадування не звучатимуть однаково. А ще до будь-якого нагадування можна додати кнопки "
            "«Відкласти» та Трекер — я навіть рахуватиму твій стрік.\n\n"
            "Тисни кнопку нижче й створюй перше нагадування."
        ),
        "help": (
            "<b>Коротко про Echo</b>\n\n"
            "Відкрий застосунок кнопкою нижче й створи нагадування — одноразове чи повторюване "
            "(щодня, щотижня або щороку). Обери час або кілька — далі я сам.\n\n"
            "Хочеш, щоб я писав у групу чи канал? Додай мене туди адміном — вони з'являться в застосунку "
            "як місце призначення, і можна буде планувати публікації для спільноти наперед.\n\n"
            "Щоб було різноманітніше, увімкни <b>Пул повідомлень</b>: напиши кілька фраз, розділи їх порожнім "
            "рядком — і я щоразу надсилатиму випадкову. Ідеально для звичок, які не хочеться робити нудними.\n\n"
            "А якщо потрібна невелика підтримка — додай <b>«Відкласти»</b>, щоб перенести нагадування на потім, "
            "або <b>Трекер</b>, щоб позначати виконане й накопичувати стрік.\n\n"
            "Загубив застосунок? Просто надішли /app."
        ),
        "app": "Твої нагадування тут:",
        "open_echo": "Відкрити Echo",
    },
    "hy": {
        "welcome": (
            "Բարև, ես Echo-ն եմ 👋 Պատկերացրու ինձ որպես քո փոքրիկ անձնական օգնական, որը հիշում է քո փոխարեն։\n\n"
            "Պարզապես ասա՝ ինչի մասին և երբ հիշեցնեմ՝ մեկ անգամ կամ կրկնվող (ամեն օր, շաբաթ կամ տարի), "
            "և ես ճիշտ ժամանակին կհիշեցնեմ։ Ունե՞ս խումբ կամ ալիք։ Ավելացրու ինձ այնտեղ որպես ադմին, "
            "և ես կկարողանամ հրապարակումներ պլանավորել քո համայնքի համար։\n\n"
            "Ուզո՞ւմ ես, որ սովորույթներդ չձանձրացնեն։ Միացրու «Հաղորդագրությունների խումբը»՝ ամեն անգամ ցանկից "
            "պատահական արտահայտություն կընտրեմ, և հիշեցումները նույնը չեն հնչի։ Ցանկացած հիշեցման կարող ես ավելացնել "
            "«Հետաձգել» կոճակը և «Հետևողը»՝ ես նույնիսկ քո շարքը կհաշվեմ։\n\n"
            "Սեղմիր ներքևի կոճակը և ստեղծիր քո առաջին հիշեցումը։"
        ),
        "help": (
            "<b>Կարճ ծանոթություն Echo-ի հետ</b>\n\n"
            "Բացիր հավելվածը ներքևի կոճակով և ստեղծիր հիշեցում՝ մեկանգամյա կամ կրկնվող "
            "(ամեն օր, շաբաթ կամ տարի)։ Ընտրիր մեկ կամ մի քանի ժամ, մնացածը իմ գործն է։\n\n"
            "Ուզո՞ւմ ես, որ գրեմ խմբում կամ ալիքում։ Ավելացրու ինձ այնտեղ որպես ադմին. այն կհայտնվի հավելվածում "
            "որպես ուղղություն, և կկարողանաս համայնքի համար հրապարակումներ պլանավորել նախապես։\n\n"
            "Որպեսզի ամեն ինչ միապաղաղ չլինի, միացրու <b>Հաղորդագրությունների խումբը</b>. գրիր մի քանի արտահայտություն, "
            "բաժանիր դրանք դատարկ տողով, և ես ամեն անգամ կուղարկեմ պատահականը։ Իդեալական է այն սովորույթների համար, "
            "որոնք չես ուզում ձանձրալի դարձնել։\n\n"
            "Իսկ եթե փոքր աջակցություն է պետք, ավելացրու <b>«Հետաձգել»</b>՝ հիշեցումը հետո տեղափոխելու համար, "
            "կամ <b>Հետևողը</b>՝ կատարվածը նշելու և շարք կուտակելու համար։\n\n"
            "Հավելվածը կորցրի՞ր։ Պարզապես ուղարկիր /app։"
        ),
        "app": "Քո հիշեցումները այստեղ են՝",
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