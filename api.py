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
from fastapi import FastAPI, File, Header, HTTPException, Path, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database as db

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("echo-api")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
STORAGE_CHANNEL_ID = os.getenv("STORAGE_CHANNEL_ID")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
RecurrenceType = Literal["none", "daily", "weekly", "yearly"]

app = FastAPI(title="Echo Reminders API")

@app.on_event("startup")
async def on_startup() -> None:
    await db.init_db()

def _parse_and_verify_init_data(init_data: str) -> dict:
    if not init_data: raise HTTPException(status_code=401, detail="Missing Telegram init data")
    pairs = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash: raise HTTPException(status_code=401, detail="Init data missing hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if BOT_TOKEN and computed_hash != received_hash:
        raise HTTPException(status_code=401, detail="Invalid init data signature")
    return pairs

def get_current_user_id(x_telegram_init_data: Optional[str] = Header(default=None)) -> int:
    if not x_telegram_init_data: raise HTTPException(status_code=401, detail="Missing X-Telegram-Init-Data header")
    pairs = _parse_and_verify_init_data(x_telegram_init_data)
    user_raw = pairs.get("user")
    if not user_raw: raise HTTPException(status_code=401, detail="No user in init data")
    return int(json.loads(user_raw)["id"])

class ReminderIn(BaseModel):
    chat_id: int
    text: str
    run_date: str
    media_url: Optional[str] = None
    snooze_enabled: bool = False
    tracker_enabled: bool = False
    recurrence: RecurrenceType = "none"
    end_date: Optional[str] = None
    use_message_pool: bool = False

class ReminderUpdate(BaseModel):
    chat_id: Optional[int] = None
    text: Optional[str] = None
    run_date: Optional[str] = None
    media_url: Optional[str] = None
    snooze_enabled: Optional[bool] = None
    tracker_enabled: Optional[bool] = None
    recurrence: Optional[RecurrenceType] = None
    end_date: Optional[str] = None
    use_message_pool: Optional[bool] = None
    is_active: Optional[bool] = None

class TestSendIn(BaseModel):
    chat_id: int
    text: str
    media_url: Optional[str] = None
    use_message_pool: bool = False
    snooze_enabled: bool = False
    tracker_enabled: bool = False

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def serve_index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/api/chats")
async def list_chats(x_telegram_init_data: Optional[str] = Header(default=None)) -> list[dict]:
    uid = get_current_user_id(x_telegram_init_data)
    return await db.get_user_chats(uid)

@app.post("/api/uploads")
async def upload_image(file: UploadFile = File(...), x_telegram_init_data: Optional[str] = Header(default=None)) -> dict:
    uid = get_current_user_id(x_telegram_init_data)
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")
    bot_instance = getattr(app.state, "bot", None)
    if bot_instance is None: raise HTTPException(status_code=503, detail="Bot is not connected.")
    data = await file.read()
    await file.close()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large (max 5 MB)")
    try:
        # Якщо в .env є ID каналу, шлемо туди, якщо немає — шлемо юзеру (як fallback)
        target_chat = int(STORAGE_CHANNEL_ID) if STORAGE_CHANNEL_ID else uid
        sent = await bot_instance.send_photo(
            chat_id=target_chat,
            photo=BufferedInputFile(data, filename=file.filename or "upload.jpg"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to store image: {exc}") from exc
    return {"media_url": sent.photo[-1].file_id}

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
    new_id = await db.create_reminder(
        user_id=uid, chat_id=payload.chat_id, text=payload.text, run_date=payload.run_date,
        media_url=payload.media_url, snooze_enabled=payload.snooze_enabled,
        tracker_enabled=payload.tracker_enabled, recurrence=payload.recurrence,
        end_date=payload.end_date, use_message_pool=payload.use_message_pool,
    )
    return await db.get_reminder(new_id)

@app.put("/api/reminders/{reminder_id}")
async def edit_reminder(reminder_id: int, payload: ReminderUpdate, x_telegram_init_data: Optional[str] = Header(default=None)) -> dict:
    uid = get_current_user_id(x_telegram_init_data)
    reminder = await db.get_reminder(reminder_id)
    if not reminder or reminder["user_id"] != uid: raise HTTPException(status_code=404, detail="Reminder not found")
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if "snooze_enabled" in fields: fields["snooze_enabled"] = int(fields["snooze_enabled"])
    if "tracker_enabled" in fields: fields["tracker_enabled"] = int(fields["tracker_enabled"])
    if "use_message_pool" in fields: fields["use_message_pool"] = int(fields["use_message_pool"])
    if "is_active" in fields: fields["is_active"] = int(fields["is_active"])
    await db.update_reminder(reminder_id, **fields)
    return await db.get_reminder(reminder_id)

@app.delete("/api/reminders/{reminder_id}")
async def remove_reminder(reminder_id: int = Path(...), x_telegram_init_data: Optional[str] = Header(default=None)) -> dict:
    uid = get_current_user_id(x_telegram_init_data)
    reminder = await db.get_reminder(reminder_id)
    if not reminder or reminder["user_id"] != uid: raise HTTPException(status_code=404, detail="Reminder not found")
    await db.delete_reminder(reminder_id)
    return {"ok": True}
