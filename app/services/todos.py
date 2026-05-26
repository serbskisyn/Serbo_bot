"""
todos.py — Async SQLite ToDo store with priority scoring.

Schema:
    id INTEGER PRIMARY KEY AUTOINCREMENT
    user_id INTEGER NOT NULL
    text TEXT NOT NULL
    source TEXT          -- manual|chat|granola|gcal
    due_date TEXT        -- ISO date YYYY-MM-DD or NULL
    status TEXT          -- open|snoozed|done|dropped
    snoozed_until TEXT   -- ISO date or NULL
    mention_count INT
    created_at TEXT
    updated_at TEXT
    last_mentioned_at TEXT
    notes TEXT

Priority score (computed at read time):
    base = 1.0 if due_in_days <= 0 (overdue/today) else 0.5
    score = base * (0.5 + min(mention_count, 10) / 10)
            * recency_decay(last_mentioned_at)
    recency_decay = 1.0 if mentioned today, 0.85 last 7d, 0.7 older
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

TODOS_DB = Path(__file__).parent.parent / "data" / "todos.db"

_WEEKDAYS_DE = {
    "mo": 0, "montag": 0,
    "di": 1, "dienstag": 1,
    "mi": 2, "mittwoch": 2,
    "do": 3, "donnerstag": 3,
    "fr": 4, "freitag": 4,
    "sa": 5, "samstag": 5,
    "so": 6, "sonntag": 6,
}


# ──────────────────────────────────────────────────────────────────────────────
# Schema & connection


_SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    snoozed_until TEXT,
    mention_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_mentioned_at TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_todos_user_status ON todos(user_id, status);
CREATE INDEX IF NOT EXISTS idx_todos_due ON todos(user_id, due_date);
"""


async def init_db() -> None:
    TODOS_DB.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(TODOS_DB) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> date:
    return datetime.now().date()


# ──────────────────────────────────────────────────────────────────────────────
# Date parsing


def parse_date(text: str) -> str | None:
    """Parse a German-ish date hint into ISO YYYY-MM-DD. None if not a date."""
    t = (text or "").strip().lower()
    if not t:
        return None

    today = _today()
    if t in ("heute", "today"):
        return today.isoformat()
    if t in ("morgen", "tomorrow"):
        return (today + timedelta(days=1)).isoformat()
    if t in ("übermorgen", "uebermorgen"):
        return (today + timedelta(days=2)).isoformat()

    if t in _WEEKDAYS_DE:
        target = _WEEKDAYS_DE[t]
        delta = (target - today.weekday()) % 7
        if delta == 0:
            delta = 7  # interpret "freitag" said on Friday as next Friday
        return (today + timedelta(days=delta)).isoformat()

    # Numeric forms: DD.MM[.YYYY], DD/MM[/YYYY], YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", t)
    if m:
        y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None

    m = re.match(r"^(\d{1,2})[.\/](\d{1,2})(?:[.\/](\d{2,4}))?$", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else today.year
        if y < 100:
            y += 2000
        try:
            parsed = date(y, mo, d)
            # If the date is in the past (without explicit year), bump to next year
            if not m.group(3) and parsed < today:
                parsed = date(y + 1, mo, d)
            return parsed.isoformat()
        except ValueError:
            return None

    return None


def _extract_trailing_date(text: str) -> tuple[str, str | None]:
    """Split the trailing date/weekday hint off the todo text.

    Returns (cleaned_text, iso_date | None).
    """
    if not text:
        return text, None
    tokens = text.rsplit(maxsplit=1)
    if len(tokens) < 2:
        # single token — could itself be a date (no text)
        if parse_date(text):
            return text, parse_date(text)
        return text, None
    head, tail = tokens[0], tokens[1]
    iso = parse_date(tail)
    if iso:
        return head, iso
    # Try last two tokens (e.g. "vorbereiten 30. mai")
    return text, None


# ──────────────────────────────────────────────────────────────────────────────
# CRUD


async def add_todo(
    user_id: int,
    text: str,
    *,
    source: str = "manual",
    due_date: str | None = None,
    notes: str | None = None,
) -> int:
    """Insert a new todo. Returns the new row id."""
    await init_db()
    now = _now_iso()
    async with aiosqlite.connect(TODOS_DB) as db:
        cur = await db.execute(
            """INSERT INTO todos
               (user_id, text, source, due_date, status, mention_count,
                created_at, updated_at, last_mentioned_at, notes)
               VALUES (?, ?, ?, ?, 'open', 1, ?, ?, ?, ?)""",
            (user_id, text.strip(), source, due_date, now, now, now, notes),
        )
        await db.commit()
        return cur.lastrowid or 0


async def mention_existing(user_id: int, text: str) -> int | None:
    """If a similar open todo exists, bump mention_count + last_mentioned_at.

    Returns the id of the matched todo, or None.
    """
    await init_db()
    norm = text.strip().lower()
    if not norm:
        return None
    async with aiosqlite.connect(TODOS_DB) as db:
        async with db.execute(
            "SELECT id, text FROM todos WHERE user_id = ? AND status = 'open'",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            if (row[1] or "").strip().lower() == norm:
                now = _now_iso()
                await db.execute(
                    """UPDATE todos
                       SET mention_count = mention_count + 1,
                           last_mentioned_at = ?,
                           updated_at = ?
                       WHERE id = ?""",
                    (now, now, row[0]),
                )
                await db.commit()
                return int(row[0])
    return None


async def mark_done(user_id: int, todo_id: int) -> bool:
    await init_db()
    async with aiosqlite.connect(TODOS_DB) as db:
        cur = await db.execute(
            """UPDATE todos SET status = 'done', updated_at = ?
               WHERE id = ? AND user_id = ? AND status IN ('open', 'snoozed')""",
            (_now_iso(), todo_id, user_id),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def drop_todo(user_id: int, todo_id: int) -> bool:
    await init_db()
    async with aiosqlite.connect(TODOS_DB) as db:
        cur = await db.execute(
            """UPDATE todos SET status = 'dropped', updated_at = ?
               WHERE id = ? AND user_id = ? AND status != 'done'""",
            (_now_iso(), todo_id, user_id),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def snooze_todo(user_id: int, todo_id: int, days: int) -> str | None:
    """Snooze for N days. Returns the new snoozed_until ISO date or None."""
    await init_db()
    if days < 1:
        return None
    until = (_today() + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(TODOS_DB) as db:
        cur = await db.execute(
            """UPDATE todos SET status = 'snoozed', snoozed_until = ?, updated_at = ?
               WHERE id = ? AND user_id = ? AND status IN ('open', 'snoozed')""",
            (until, _now_iso(), todo_id, user_id),
        )
        await db.commit()
        return until if (cur.rowcount or 0) > 0 else None


async def _wake_snoozed(user_id: int) -> None:
    """Move snoozed todos whose snoozed_until <= today back to 'open'."""
    today_iso = _today().isoformat()
    async with aiosqlite.connect(TODOS_DB) as db:
        await db.execute(
            """UPDATE todos SET status = 'open', snoozed_until = NULL, updated_at = ?
               WHERE user_id = ? AND status = 'snoozed' AND snoozed_until <= ?""",
            (_now_iso(), user_id, today_iso),
        )
        await db.commit()


async def list_todos(
    user_id: int,
    scope: str = "all",
) -> list[dict[str, Any]]:
    """Return open todos matching the scope, sorted by priority desc.

    scope ∈ {today, week, all}
    """
    await init_db()
    await _wake_snoozed(user_id)
    today = _today()

    where = "user_id = ? AND status = 'open'"
    params: list[Any] = [user_id]

    if scope == "today":
        where += " AND (due_date IS NULL OR due_date <= ?)"
        params.append(today.isoformat())
    elif scope == "week":
        week_end = (today + timedelta(days=7)).isoformat()
        where += " AND (due_date IS NULL OR due_date <= ?)"
        params.append(week_end)
    # else "all" → no extra filter

    async with aiosqlite.connect(TODOS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""SELECT id, text, source, due_date, mention_count,
                       last_mentioned_at, notes, created_at
               FROM todos
               WHERE {where}""",
            params,
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    for r in rows:
        r["priority"] = _priority_score(r, today)
    rows.sort(key=lambda r: r["priority"], reverse=True)
    return rows


async def get_todo(user_id: int, todo_id: int) -> dict | None:
    await init_db()
    async with aiosqlite.connect(TODOS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM todos WHERE id = ? AND user_id = ?",
            (todo_id, user_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def stats(user_id: int) -> dict:
    """Counts by status. For /todo summary or briefings."""
    await init_db()
    async with aiosqlite.connect(TODOS_DB) as db:
        async with db.execute(
            "SELECT status, COUNT(*) FROM todos WHERE user_id = ? GROUP BY status",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    out = {"open": 0, "snoozed": 0, "done": 0, "dropped": 0}
    for status, count in rows:
        out[status] = int(count)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Priority scoring


def _priority_score(row: dict, today: date) -> float:
    """Compute the runtime priority. Used to sort `list_todos`."""
    due = row.get("due_date")
    if due:
        try:
            due_d = date.fromisoformat(due)
            due_in = (due_d - today).days
        except Exception:
            due_in = 999
    else:
        due_in = 999

    base = 1.0 if due_in <= 0 else (0.7 if due_in <= 3 else 0.5)

    mentions = int(row.get("mention_count") or 1)
    mention_factor = 0.5 + min(mentions, 10) / 10.0  # 0.6 .. 1.5

    last = row.get("last_mentioned_at")
    decay = 1.0
    if last:
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            age_days = (datetime.now(tz=timezone.utc) - last_dt).days
            if age_days <= 1:
                decay = 1.0
            elif age_days <= 7:
                decay = 0.85
            else:
                decay = 0.7
        except Exception:
            decay = 0.85

    return round(base * mention_factor * decay, 3)
