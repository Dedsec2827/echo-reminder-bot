"""
database.py
------------
Асинхронний шар роботи з PostgreSQL (через asyncpg) для бота Echo.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "")

RECURRENCE_INTERVALS: dict[str, timedelta] = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}

RECURRING_TYPES: frozenset[str] = frozenset({"daily", "weekly", "yearly"})

_pool: Optional[asyncpg.Pool] = None


def utcnow() -> datetime:
    """Поточний момент як offset-aware datetime у UTC."""
    return datetime.now(timezone.utc)


def parse_iso_utc(value: str) -> datetime:
    """Парсить ISO-рядок і завжди повертає offset-aware datetime в UTC."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _row_to_dict(row: Optional[asyncpg.Record]) -> Optional[dict[str, Any]]:
    return dict(row) if row else None


def serialize_daily_times(daily_times: Optional[list[str]]) -> Optional[str]:
    return json.dumps(daily_times) if daily_times else None


def parse_daily_times(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
        return [t for t in data if isinstance(t, str)]
    except (json.JSONDecodeError, TypeError):
        return []


def _reminder_row_to_dict(row: Optional[asyncpg.Record]) -> Optional[dict[str, Any]]:
    if not row:
        return None
    data = dict(row)
    data["daily_times"] = parse_daily_times(data.get("daily_times"))
    return data


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=10,
            command_timeout=15,  # fail fast instead of hanging a request on a stuck query
            max_inactive_connection_lifetime=300,
            # Disables asyncpg's client-side prepared statement cache. Needed for poolers like
            # PgBouncer (transaction mode) and to avoid InvalidCachedStatementError after schema
            # changes, at a small cost to per-query performance.
            statement_cache_size=0,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _run_migrations(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'reminders'"
    )
    columns = {r["column_name"] for r in rows}
    migrations = {
        "recurrence": "ALTER TABLE reminders ADD COLUMN recurrence TEXT NOT NULL DEFAULT 'none'",
        "end_date": "ALTER TABLE reminders ADD COLUMN end_date TEXT",
        "use_message_pool": "ALTER TABLE reminders ADD COLUMN use_message_pool INTEGER NOT NULL DEFAULT 0",
        "media_message_id": "ALTER TABLE reminders ADD COLUMN media_message_id BIGINT",
        "daily_times": "ALTER TABLE reminders ADD COLUMN daily_times TEXT",
    }
    for column, ddl in migrations.items():
        if column not in columns:
            await conn.execute(ddl)

    user_rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'users'"
    )
    user_columns = {r["column_name"] for r in user_rows}
    user_migrations = {
        "language": "ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'en'",
    }
    for column, ddl in user_migrations.items():
        if column not in user_columns:
            await conn.execute(ddl)


async def init_db() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id     BIGINT PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                created_at  TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                chat_id     BIGINT PRIMARY KEY,
                owner_id    BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
                title       TEXT NOT NULL,
                chat_type   TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id                SERIAL PRIMARY KEY,
                user_id           BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
                chat_id           BIGINT NOT NULL REFERENCES chats (chat_id) ON DELETE CASCADE,
                text              TEXT NOT NULL,
                media_url         TEXT,
                run_date          TEXT NOT NULL,
                recurrence        TEXT NOT NULL DEFAULT 'none',
                end_date          TEXT,
                use_message_pool  INTEGER NOT NULL DEFAULT 0,
                is_active         INTEGER NOT NULL DEFAULT 1,
                is_sent           INTEGER NOT NULL DEFAULT 0,
                snooze_enabled    INTEGER NOT NULL DEFAULT 0,
                tracker_enabled   INTEGER NOT NULL DEFAULT 0,
                streak_count      INTEGER NOT NULL DEFAULT 0,
                created_at        TEXT NOT NULL
            )
            """
        )
        await _run_migrations(conn)
        # Speeds up the scheduler's due-reminder poll and the per-user list/chat endpoints.
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (is_active, is_sent, run_date)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders (user_id, is_active)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_chats_owner ON chats (owner_id)")


async def upsert_user(user_id: int, username: Optional[str], first_name: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO users (user_id, username, first_name, created_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
        """,
        user_id, username, first_name, utcnow().isoformat(),
    )


async def get_user(user_id: int) -> Optional[dict]:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
    return _row_to_dict(row)


async def get_user_language(user_id: int) -> str:
    pool = await get_pool()
    value = await pool.fetchval("SELECT language FROM users WHERE user_id = $1", user_id)
    return value or "en"


async def set_user_language(user_id: int, language: str) -> None:
    pool = await get_pool()
    await pool.execute("UPDATE users SET language = $1 WHERE user_id = $2", language, user_id)


async def upsert_chat(chat_id: int, owner_id: int, title: str, chat_type: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO chats (chat_id, owner_id, title, chat_type, created_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (chat_id) DO UPDATE SET
            title = excluded.title,
            chat_type = excluded.chat_type
        """,
        chat_id, owner_id, title, chat_type, utcnow().isoformat(),
    )


async def get_user_chats(owner_id: int) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM chats WHERE owner_id = $1 ORDER BY chat_type, title", owner_id
    )
    return [_row_to_dict(r) for r in rows]


async def get_chat(chat_id: int) -> Optional[dict]:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM chats WHERE chat_id = $1", chat_id)
    return _row_to_dict(row)


async def delete_chat(chat_id: int) -> None:
    """Drops a routing destination (e.g. the bot was removed from a group/channel).
    Reminders pointed at it are removed too via the chats->reminders ON DELETE CASCADE."""
    pool = await get_pool()
    await pool.execute("DELETE FROM chats WHERE chat_id = $1", chat_id)


async def create_reminder(
    user_id: int, chat_id: int, text: str, run_date: str, media_url: Optional[str] = None,
    media_message_id: Optional[int] = None,
    snooze_enabled: bool = False, tracker_enabled: bool = False, recurrence: str = "none",
    end_date: Optional[str] = None, use_message_pool: bool = False,
    daily_times: Optional[list[str]] = None,
) -> int:
    pool = await get_pool()
    new_id = await pool.fetchval(
        """
        INSERT INTO reminders
            (user_id, chat_id, text, media_url, media_message_id, run_date, recurrence, end_date,
             use_message_pool, snooze_enabled, tracker_enabled, daily_times, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        RETURNING id
        """,
        user_id, chat_id, text, media_url, media_message_id, run_date, recurrence, end_date,
        int(use_message_pool), int(snooze_enabled), int(tracker_enabled),
        serialize_daily_times(daily_times),
        utcnow().isoformat(),
    )
    return new_id


async def create_reminders_batch(
    user_id: int, chat_id: int, text: str, run_dates: list[str], media_url: Optional[str] = None,
    media_message_id: Optional[int] = None, snooze_enabled: bool = False, tracker_enabled: bool = False,
    use_message_pool: bool = False,
) -> list[int]:
    """Creates one recurrence='none' row per run_date in a single transaction. Used when a
    "Once" reminder is scheduled with several times - each becomes its own independent
    one-off row so the scheduler's normal due-reminder logic needs no changes."""
    pool = await get_pool()
    ids: list[int] = []
    created_at = utcnow().isoformat()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for run_date in run_dates:
                new_id = await conn.fetchval(
                    """
                    INSERT INTO reminders
                        (user_id, chat_id, text, media_url, media_message_id, run_date, recurrence,
                         end_date, use_message_pool, snooze_enabled, tracker_enabled, daily_times, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, 'none', NULL, $7, $8, $9, NULL, $10)
                    RETURNING id
                    """,
                    user_id, chat_id, text, media_url, media_message_id, run_date,
                    int(use_message_pool), int(snooze_enabled), int(tracker_enabled), created_at,
                )
                ids.append(new_id)
    return ids


async def media_still_referenced(media_message_id: int, exclude_id: Optional[int] = None) -> bool:
    """True if some OTHER reminder still points at this channel media (multi-time "Once"
    reminders can share one photo across several rows), so callers know it's unsafe to
    delete the channel post yet."""
    pool = await get_pool()
    if exclude_id is not None:
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM reminders WHERE media_message_id = $1 AND id != $2",
            media_message_id, exclude_id,
        )
    else:
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM reminders WHERE media_message_id = $1", media_message_id
        )
    return bool(count)


async def get_reminders_by_user(user_id: int) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM reminders WHERE user_id = $1 AND is_active = 1 ORDER BY run_date ASC",
        user_id,
    )
    return [_reminder_row_to_dict(r) for r in rows]


async def get_reminder(reminder_id: int) -> Optional[dict]:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM reminders WHERE id = $1", reminder_id)
    return _reminder_row_to_dict(row)


async def update_reminder(reminder_id: int, **fields: Any) -> None:
    if not fields:
        return
    allowed = {
        "chat_id", "text", "media_url", "media_message_id", "run_date", "recurrence", "end_date",
        "use_message_pool", "snooze_enabled", "tracker_enabled", "is_active", "daily_times",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "daily_times" in updates and not isinstance(updates["daily_times"], str):
        updates["daily_times"] = serialize_daily_times(updates["daily_times"])
    pool = await get_pool()
    set_clause = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(updates))
    values = list(updates.values())
    values.append(reminder_id)
    await pool.execute(
        f"UPDATE reminders SET {set_clause} WHERE id = ${len(values)}", *values
    )


async def delete_reminder(reminder_id: int) -> None:
    pool = await get_pool()
    await pool.execute("DELETE FROM reminders WHERE id = $1", reminder_id)


async def get_due_reminders() -> list[dict]:
    # Filtering by run_date in SQL (instead of fetching every active reminder and
    # filtering in Python) keeps each poll cheap as the table grows.
    now_iso = utcnow().isoformat()
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM reminders WHERE is_active = 1 AND is_sent = 0 AND run_date <= $1",
        now_iso,
    )
    return [_reminder_row_to_dict(r) for r in rows]


async def mark_reminder_sent(reminder_id: int, keep_active: bool) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE reminders SET is_sent = 1, is_active = $1 WHERE id = $2",
        int(keep_active), reminder_id,
    )


def compute_next_run_date(
    current_run_date_iso: str, recurrence: str, daily_times: Optional[list[str]] = None
) -> str:
    if recurrence not in RECURRING_TYPES:
        raise ValueError(f"Непідтримуваний тип повтору: {recurrence!r}")

    now = utcnow()

    if recurrence == "daily" and daily_times:
        # Multiple times per day: pick the next one still ahead today, or the
        # earliest time tomorrow if every slot for today has already fired.
        parsed: list[tuple[int, int]] = []
        for t in daily_times:
            try:
                hh, mm = t.split(":")
                parsed.append((int(hh), int(mm)))
            except (ValueError, AttributeError):
                continue
        if parsed:
            parsed.sort()
            for hh, mm in parsed:
                candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if candidate > now:
                    return candidate.isoformat()
            hh, mm = parsed[0]
            candidate = (now + timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            return candidate.isoformat()

    def _advance(dt: datetime) -> datetime:
        if recurrence == "yearly":
            try:
                return dt.replace(year=dt.year + 1)
            except ValueError:
                return dt.replace(year=dt.year + 1, day=28)
        return dt + RECURRENCE_INTERVALS[recurrence]

    next_date = _advance(parse_iso_utc(current_run_date_iso))
    while next_date <= now:
        next_date = _advance(next_date)
    return next_date.isoformat()


async def reschedule_recurring_reminder(reminder_id: int, next_run_date: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE reminders SET run_date = $1, is_sent = 0, is_active = 1 WHERE id = $2",
        next_run_date, reminder_id,
    )


async def snooze_reminder(reminder_id: int, minutes: int = 15) -> str:
    new_run_date = (utcnow() + timedelta(minutes=minutes)).isoformat()
    pool = await get_pool()
    await pool.execute(
        "UPDATE reminders SET run_date = $1, is_sent = 0, is_active = 1 WHERE id = $2",
        new_run_date, reminder_id,
    )
    return new_run_date


async def increment_streak(reminder_id: int, deactivate: bool = True) -> int:
    pool = await get_pool()
    if deactivate:
        query = (
            "UPDATE reminders SET streak_count = streak_count + 1, is_active = 0 "
            "WHERE id = $1 RETURNING streak_count"
        )
    else:
        query = (
            "UPDATE reminders SET streak_count = streak_count + 1 "
            "WHERE id = $1 RETURNING streak_count"
        )
    row = await pool.fetchrow(query, reminder_id)
    return row["streak_count"] if row else 0