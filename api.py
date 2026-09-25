"""
api.py
------
FastAPI застосунок для Echo.
"""

import hashlib
import hmac
import json
import logging
import os
from typing import Literal, Optional
from urllib.parse import parse_qsl

from aiogram.types import BufferedInputFile
from dotenv import load_dotenv
from fastapi import FastAPI, File, Header, HTTPException, Path, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import database as db

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("echo-api")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# Client compresses to WebP already (see index.html compressImage()); JPEG/PNG accepted
# as a fallback for browsers that can't encode WebP.
ALLOWED_IMAGE_TYPES = {"image/webp", "image/jpeg", "image/png"}
RecurrenceType = Literal["none", "daily", "weekly", "yearly"]

app = FastAPI(title="Echo Reminders API")


@app.on_event("startup")
async def on_startup() -> None:
    await db.init_db()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Catch-all so a bug or a Telegram/DB hiccup in one route never crashes or hangs the process.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def _parse_and_verify_init_data(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram init data")
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        raise HTTPException(status_code=401, detail="Malformed init data")
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Init data missing hash")
    if not BOT_TOKEN:
        # Fail closed: without a token the signature can't be verified at all.
        raise HTTPException(status_code=503, detail="Server is not configured")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid init data signature")
    return pairs


def get_current_user_id(x_telegram_init_data: Optional[str] = Header(default=None)) -> int:
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing X-Telegram-Init-Data header")
    pairs = _parse_and_verify_init_data(x_telegram_init_data)
    user_raw = pairs.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="No user in init data")
    try:
        return int(json.loads(user_raw)["id"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Malformed user in init data")


class ReminderIn(BaseModel):
    chat_id: int
    text: str = ""
    run_date: str
    media_url: Optional[str] = None
    media_message_id: Optional[int] = None
    snooze_enabled: bool = False
    tracker_enabled: bool = False
    recurrence: RecurrenceType = "none"
    end_date: Optional[str] = None
    use_message_pool: bool = False
    daily_times: Optional[list[str]] = None
    # Minutes EAST of UTC for the user's local timezone (JS: -getTimezoneOffset()).
    # Needed so "HH:MM" strings from the client are combined with the date in local time.
    tz_offset_minutes: Optional[int] = Field(default=None, ge=-840, le=840)
    snooze_options: Optional[list[int]] = Field(default=[15, 60, 1440])
    # 0=Monday..6=Sunday
    exclude_weekdays: Optional[list[int]] = None
    exclude_dates: Optional[list[str]] = None


class ReminderUpdate(BaseModel):
    chat_id: Optional[int] = None
    text: Optional[str] = None
    run_date: Optional[str] = None
    media_url: Optional[str] = None
    media_message_id: Optional[int] = None
    snooze_enabled: Optional[bool] = None
    tracker_enabled: Optional[bool] = None
    recurrence: Optional[RecurrenceType] = None
    end_date: Optional[str] = None
    use_message_pool: Optional[bool] = None
    is_active: Optional[bool] = None
    daily_times: Optional[list[str]] = None
    tz_offset_minutes: Optional[int] = Field(default=None, ge=-840, le=840)
    snooze_options: Optional[list[int]] = Field(default=[15, 60, 1440])
    exclude_weekdays: Optional[list[int]] = None
    exclude_dates: Optional[list[str]] = None


class TestSendIn(BaseModel):
    chat_id: int
    text: str = ""
    media_url: Optional[str] = None
    use_message_pool: bool = False
    snooze_enabled: bool = False
    tracker_enabled: bool = False


class UploadDeleteIn(BaseModel):
    media_url: Optional[str] = None
    media_message_id: Optional[int] = None


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def serve_index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/health")
async def health() -> dict:
    """Lightweight keep-alive endpoint for external cron pings (no auth, no DB, no frontend)."""
    return {"status": "ok"}


@app.get("/api/chats")
async def list_chats(x_telegram_init_data: Optional[str] = Header(default=None)) -> list[dict]:
    uid = get_current_user_id(x_telegram_init_data)
    return await db.get_user_chats(uid)


@app.get("/api/me")
async def get_me(x_telegram_init_data: Optional[str] = Header(default=None)) -> dict:
    """Lets the Mini App mirror the language the user picked in the bot chat."""
    uid = get_current_user_id(x_telegram_init_data)
    language = await db.get_user_language(uid)
    return {"user_id": uid, "language": language}


async def _delete_channel_message(bot_instance, message_id: Optional[int], exclude_reminder_id: Optional[int] = None) -> None:
    """Best-effort removal of a stored photo's channel post; never raises.
    A multi-time "Once" reminder can share one media_message_id across several rows, so
    this skips deletion while any OTHER reminder (besides exclude_reminder_id) still uses it."""
    if not bot_instance or not message_id or not CHANNEL_ID:
        return
    if await db.media_still_referenced(message_id, exclude_id=exclude_reminder_id):
        return
    try:
        await bot_instance.delete_message(chat_id=int(CHANNEL_ID), message_id=message_id)
    except Exception:
        logger.warning("Couldn't delete channel message %s", message_id, exc_info=True)


@app.post("/api/uploads")
async def upload_image(file: UploadFile = File(...), x_telegram_init_data: Optional[str] = Header(default=None)) -> dict:
    get_current_user_id(x_telegram_init_data)
    if not file.content_type or file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG or WebP images are allowed")
    if not CHANNEL_ID:
        raise HTTPException(status_code=503, detail="Storage channel is not configured.")
    bot_instance = getattr(app.state, "bot", None)
    if bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot is not connected.")
    data = await file.read()
    await file.close()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large (max 5 MB)")
    try:
        # BufferedInputFile streams straight from memory - the compressed photo is
        # never written to disk, and Telegram itself becomes the storage backend.
        sent = await bot_instance.send_photo(
            chat_id=int(CHANNEL_ID),
            photo=BufferedInputFile(data, filename=file.filename or "upload.jpg"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to store image: {exc}") from exc
    return {"media_url": sent.photo[-1].file_id, "media_message_id": sent.message_id}


@app.delete("/api/uploads")
async def delete_upload(payload: UploadDeleteIn, x_telegram_init_data: Optional[str] = Header(default=None)) -> dict:
    get_current_user_id(x_telegram_init_data)
    bot_instance = getattr(app.state, "bot", None)
    await _delete_channel_message(bot_instance, payload.media_message_id)
    return {"ok": True}


@app.get("/api/media/{file_id}")
async def get_media(file_id: str) -> StreamingResponse:
    # Reminder photos are only reachable via their unguessable Telegram file_id, so this
    # route is left unauthenticated by design - a plain <img src> can't attach the
    # X-Telegram-Init-Data header FastAPI's other routes require.
    bot_instance = getattr(app.state, "bot", None)
    if bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot is not connected.")
    try:
        tg_file = await bot_instance.get_file(file_id)
        buf = await bot_instance.download_file(tg_file.file_path)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Image not found") from exc
    # Telegram always re-encodes photos sent via send_photo as JPEG, regardless of the
    # original upload format, so this content type is accurate for every stored image.
    return StreamingResponse(buf, media_type="image/jpeg")


async def _require_own_chat(uid: int, chat_id: int) -> None:
    chat = await db.get_chat(chat_id)
    if not chat or chat["owner_id"] != uid:
        raise HTTPException(status_code=404, detail="Route not found")


@app.post("/api/test-send")
async def test_send(payload: TestSendIn, x_telegram_init_data: Optional[str] = Header(default=None)) -> dict:
    uid = get_current_user_id(x_telegram_init_data)
    chat = await db.get_chat(payload.chat_id)
    if not chat or chat["owner_id"] != uid: raise HTTPException(status_code=404, detail="Route not found")
    bot_instance = getattr(app.state, "bot", None)
    if bot_instance is None: raise HTTPException(status_code=503, detail="Bot is not connected.")
    from bot import send_test_message
    try:
        await send_test_message(bot_instance, payload.chat_id, payload.text, media_url=payload.media_url, use_message_pool=payload.use_message_pool, snooze_enabled=payload.snooze_enabled, tracker_enabled=payload.tracker_enabled)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to send: {exc}") from exc
    return {"ok": True}

@app.get("/api/reminders")
async def list_reminders(x_telegram_init_data: Optional[str] = Header(default=None)) -> list[dict]:
    uid = get_current_user_id(x_telegram_init_data)
    return await db.get_reminders_by_user(uid)

@app.post("/api/reminders")
async def create_reminder(payload: ReminderIn, x_telegram_init_data: Optional[str] = Header(default=None)) -> dict:
    uid = get_current_user_id(x_telegram_init_data)
    await _require_own_chat(uid, payload.chat_id)
    new_id = await db.create_reminder(
        user_id=uid, chat_id=payload.chat_id, text=payload.text, run_date=payload.run_date,
        media_url=payload.media_url, media_message_id=payload.media_message_id,
        snooze_enabled=payload.snooze_enabled,
        tracker_enabled=payload.tracker_enabled, recurrence=payload.recurrence,
        end_date=payload.end_date, use_message_pool=payload.use_message_pool,
        daily_times=payload.daily_times, tz_offset_minutes=payload.tz_offset_minutes or 0,
        snooze_options=payload.snooze_options,
        exclude_weekdays=payload.exclude_weekdays, exclude_dates=payload.exclude_dates,
    )
    return await db.get_reminder(new_id)

@app.put("/api/reminders/{reminder_id}")
async def edit_reminder(reminder_id: int, payload: ReminderUpdate, x_telegram_init_data: Optional[str] = Header(default=None)) -> dict:
    uid = get_current_user_id(x_telegram_init_data)
    reminder = await db.get_reminder(reminder_id)
    if not reminder or reminder["user_id"] != uid: raise HTTPException(status_code=404, detail="Reminder not found")
    if payload.chat_id is not None: await _require_own_chat(uid, payload.chat_id)
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if "snooze_enabled" in fields: fields["snooze_enabled"] = int(fields["snooze_enabled"])
    if "tracker_enabled" in fields: fields["tracker_enabled"] = int(fields["tracker_enabled"])
    if "use_message_pool" in fields: fields["use_message_pool"] = int(fields["use_message_pool"])
    if "is_active" in fields: fields["is_active"] = int(fields["is_active"])
    old_media_message_id = reminder.get("media_message_id")
    await db.update_reminder(reminder_id, **fields)
    if "media_url" in fields and fields["media_url"] != reminder.get("media_url"):
        # Photo was replaced or cleared - drop the old channel post instead of the old file.
        await _delete_channel_message(getattr(app.state, "bot", None), old_media_message_id, exclude_reminder_id=reminder_id)
    return await db.get_reminder(reminder_id)

@app.delete("/api/reminders/{reminder_id}")
async def remove_reminder(reminder_id: int = Path(...), x_telegram_init_data: Optional[str] = Header(default=None)) -> dict:
    uid = get_current_user_id(x_telegram_init_data)
    reminder = await db.get_reminder(reminder_id)
    if not reminder or reminder["user_id"] != uid: raise HTTPException(status_code=404, detail="Reminder not found")
    await db.delete_reminder(reminder_id)
    await _delete_channel_message(getattr(app.state, "bot", None), reminder.get("media_message_id"), exclude_reminder_id=reminder_id)
    return {"ok": True}