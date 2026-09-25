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

DEFAULT_SNOOZE_OPTIONS: list[int] = [15, 60, 1440]

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


def serialize_snooze_options(snooze_options: Optional[list[int]]) -> Optional[str]:
    return json.dumps(snooze_options) if snooze_options else None


def parse_snooze_options(value: Optional[str]) -> list[int]:
    if not value:
        return list(DEFAULT_SNOOZE_OPTIONS)
    try:
        data = json.loads(value)
        result = [int(v) for v in data if isinstance(v, (int, float)) and not isinstance(v, bool)]
        return result if result else list(DEFAULT_SNOOZE_OPTIONS)
    except (json.JSONDecodeError, TypeError, ValueError):
        return list(DEFAULT_SNOOZE_OPTIONS)


def serialize_exclude_weekdays(exclude_weekdays: Optional[list[int]]) -> Optional[str]:
    return json.dumps(exclude_weekdays) if exclude_weekdays else None


def parse_exclude_weekdays(value: Optional[str]) -> list[int]:
    if not value:
        return []
    try:
        data = json.loads(value)
        return [int(v) for v in data if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= int(v) <= 6]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def serialize_exclude_dates(exclude_dates: Optional[list[str]]) -> Optional[str]:
    return json.dumps(exclude_dates) if exclude_dates else None


def parse_exclude_dates(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
        return [d for d in data if isinstance(d, str)]
    except (json.JSONDecodeError, TypeError):
        return []


def _reminder_row_to_dict(row: Optional[asyncpg.Record]) -> Optional[dict[str, Any]]:
    if not row:
        return None
    data = dict(row)
    data["daily_times"] = parse_daily_times(data.get("daily_times"))
    data["snooze_options"] = parse_snooze_options(data.get("snooze_options"))
    data["exclude_weekdays"] = parse_exclude_weekdays(data.get("exclude_weekdays"))
    data["exclude_dates"] = parse_exclude_dates(data.get("exclude_dates"))
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
        "tz_offset_minutes": "ALTER TABLE reminders ADD COLUMN tz_offset_minutes INTEGER NOT NULL DEFAULT 0",
        "snooze_options": "ALTER TABLE reminders ADD COLUMN snooze_options TEXT DEFAULT '[15, 60, 1440]'",
        "exclude_weekdays": "ALTER TABLE reminders ADD COLUMN exclude_weekdays TEXT",
        "exclude_dates": "ALTER TABLE reminders ADD COLUMN exclude_dates TEXT",
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
                tz_offset_minutes INTEGER NOT NULL DEFAULT 0,
                snooze_options    TEXT DEFAULT '[15, 60, 1440]',
                exclude_weekdays  TEXT,
                exclude_dates     TEXT,
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
    daily_times: Optional[list[str]] = None, tz_offset_minutes: int = 0,
    snooze_options: Optional[list[int]] = None,
    exclude_weekdays: Optional[list[int]] = None, exclude_dates: Optional[list[str]] = None,
) -> int:
    pool = await get_pool()
    new_id = await pool.fetchval(
        """
        INSERT INTO reminders
            (user_id, chat_id, text, media_url, media_message_id, run_date, recurrence, end_date,
             use_message_pool, snooze_enabled, tracker_enabled, daily_times, tz_offset_minutes,
             snooze_options, exclude_weekdays, exclude_dates, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
        RETURNING id
        """,
        user_id, chat_id, text, media_url, media_message_id, run_date, recurrence, end_date,
        int(use_message_pool), int(snooze_enabled), int(tracker_enabled),
        serialize_daily_times(daily_times), tz_offset_minutes or 0,
        serialize_snooze_options(snooze_options),
        serialize_exclude_weekdays(exclude_weekdays), serialize_exclude_dates(exclude_dates),
        utcnow().isoformat(),
    )
    return new_id


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
        "tz_offset_minutes", "snooze_options", "exclude_weekdays", "exclude_dates",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "daily_times" in updates and not isinstance(updates["daily_times"], str):
        updates["daily_times"] = serialize_daily_times(updates["daily_times"])
    if "snooze_options" in updates and not isinstance(updates["snooze_options"], str):
        updates["snooze_options"] = serialize_snooze_options(updates["snooze_options"])
    if "exclude_weekdays" in updates and not isinstance(updates["exclude_weekdays"], str):
        updates["exclude_weekdays"] = serialize_exclude_weekdays(updates["exclude_weekdays"])
    if "exclude_dates" in updates and not isinstance(updates["exclude_dates"], str):
        updates["exclude_dates"] = serialize_exclude_dates(updates["exclude_dates"])
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
    current_run_date_iso: str, recurrence: str, daily_times: Optional[list[str]] = None,
    tz_offset_minutes: int = 0, exclude_weekdays: Optional[list[int]] = None,
    exclude_dates: Optional[list[str]] = None,
) -> Optional[str]:
    now = utcnow()
    offset = timedelta(minutes=tz_offset_minutes or 0)
    now_local = now + offset
    exclude_weekdays = exclude_weekdays or []
    exclude_dates = exclude_dates or []

    def _is_excluded(candidate_utc: datetime) -> bool:
        # 0=Monday..6=Sunday, matching Python's datetime.weekday().
        candidate_local = candidate_utc + offset
        if candidate_local.weekday() in exclude_weekdays:
            return True
        if candidate_local.date().isoformat() in exclude_dates:
            return True
        return False

    # Parse + sort daily_times once, up front, for EVERY recurrence type (daily, weekly,
    # yearly, none) - previously this was only done for "daily"/"none", so "weekly" and
    # "yearly" silently ignored multi-time schedules entirely.
    parsed_times: list[tuple[int, int]] = []
    for t in (daily_times or []):
        try:
            hh, mm = t.split(":")
            parsed_times.append((int(hh), int(mm)))
        except (ValueError, AttributeError):
            continue
    parsed_times.sort()

    if parsed_times and recurrence == "none":
        # "none": multi-time single-day "Once" reminder - all remaining slots share
        # the same local date, so if that date is excluded none of them can fire.
        base_local = parse_iso_utc(current_run_date_iso) + offset
        for hh, mm in parsed_times:
            candidate_local = base_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            candidate_utc = candidate_local - offset
            if candidate_utc > now and not _is_excluded(candidate_utc):
                return candidate_utc.isoformat()
        return None

    if recurrence not in RECURRING_TYPES:
        raise ValueError(f"Непідтримуваний тип повтору: {recurrence!r}")

    def _advance_day(day_local: datetime) -> datetime:
        """Jump a *local* day marker forward by one recurrence interval (used to pick the
        next candidate day once every slot on the current candidate day has passed)."""
        if recurrence == "yearly":
            try:
                return day_local.replace(year=day_local.year + 1)
            except ValueError:
                return day_local.replace(year=day_local.year + 1, day=28)
        if recurrence == "weekly":
            return day_local + timedelta(days=7)
        return day_local + timedelta(days=1)  # daily

    if parsed_times:
        # Multiple times per local day, for daily/weekly/yearly alike: iterate through the
        # day's slots (sorted) to find the next still-ahead, non-excluded UTC moment; if every
        # slot on the current candidate day has already passed (or the day is excluded), use
        # _advance_day to jump to the next valid candidate day. 366-iteration failsafe against
        # infinite loops (e.g. every remaining candidate excluded).
        #
        # "daily" anchors the search on "now" so a stale current_run_date (e.g. after bot
        # downtime) doesn't burn iterations catching up. "weekly"/"yearly" anchor on
        # current_run_date's local day instead, so the target weekday / day-of-year is
        # preserved, then step forward by the interval from there.
        day_local = now_local if recurrence == "daily" else parse_iso_utc(current_run_date_iso) + offset
        iterations = 0
        while iterations < 366:
            for hh, mm in parsed_times:
                candidate_local = day_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
                candidate_utc = candidate_local - offset
                if candidate_utc > now and not _is_excluded(candidate_utc):
                    return candidate_utc.isoformat()
            day_local = _advance_day(day_local)
            iterations += 1
        # Failsafe exhausted - every day in range was excluded or already passed.
        return None

    # No daily_times (or none of them parsed): single slot per period, just advance the
    # timestamp itself by one recurrence interval at a time.
    def _advance(dt: datetime) -> datetime:
        if recurrence == "yearly":
            try:
                return dt.replace(year=dt.year + 1)
            except ValueError:
                return dt.replace(year=dt.year + 1, day=28)
        return dt + RECURRENCE_INTERVALS[recurrence]

    # Advance candidate-by-candidate (366-iteration failsafe) until one lands after
    # "now" on a non-excluded local date.
    next_date = _advance(parse_iso_utc(current_run_date_iso))
    iterations = 0
    while iterations < 366:
        if next_date > now and not _is_excluded(next_date):
            return next_date.isoformat()
        next_date = _advance(next_date)
        iterations += 1
    # Failsafe exhausted - every candidate in range was excluded or already passed.
    return None


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