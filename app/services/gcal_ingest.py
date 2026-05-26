"""
gcal_ingest.py — Pull next-7-days calendar events and queue prep-todos.

Strategy (cheap, no LLM):
  • Read events from both configured calendars (Gmail + Workspace)
  • Match titles against German + English action-prefix keywords
    ("vorbereiten", "review", "präsentation", "pitch", "demo", "interview", …)
  • For positively-matched events, create a "<keyword>: <title>" todo
    due ONE DAY before the event start (preparation buffer)

The match is intentionally conservative so we don't spam todos for
recurring stand-ups. If an event needs prep, the human usually puts
that word in the title.

source="gcal" so the briefing can show calendar-origin todos with
a distinct badge.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from app.config import GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2
from app.services import todos as todos_svc

logger = logging.getLogger(__name__)


# Title-keywords that trigger a prep-todo. German + English mixed.
_PREP_KEYWORDS = (
    "vorbereit", "prepare",
    "review", "feedback",
    "präsentation", "presentation", "pitch", "demo", "deck",
    "interview",
    "1:1", "1on1", "one-on-one",
    "workshop", "training",
    "offsite", "kickoff", "kick-off",
    "all-hands", "allhands",
    "deadline", "release", "go-live", "launch",
)

# Patterns that mean "don't auto-create a todo" — recurring stand-ups,
# blockers, focus blocks, etc.
_IGNORE_PATTERNS = (
    r"\bstand[- ]?up\b",
    r"\bdaily\b",
    r"\blunch\b",
    r"\bbreak\b",
    r"\bblock(er|ed|ing)?\b",
    r"\bfocus\b",
    r"\bdeep work\b",
    r"\boff\b",
    r"\burlaub\b",
    r"\bfrei\b",
)


def _matches_prep(title: str) -> str | None:
    """Return the matched keyword if the title looks prep-worthy, else None."""
    if not title:
        return None
    low = title.lower()
    for pat in _IGNORE_PATTERNS:
        if re.search(pat, low):
            return None
    for kw in _PREP_KEYWORDS:
        if kw in low:
            return kw
    return None


def _event_start(event: dict) -> datetime | None:
    start = event.get("start") or {}
    if "dateTime" in start:
        try:
            return datetime.fromisoformat(start["dateTime"]).astimezone(timezone.utc)
        except Exception:
            return None
    if "date" in start:
        try:
            return datetime.fromisoformat(start["date"] + "T00:00:00+00:00")
        except Exception:
            return None
    return None


def _build_todo_text(event: dict, kw: str) -> str:
    title = (event.get("summary") or "").strip() or "(unbenannter Termin)"
    return f"Vorbereiten: {title}"


async def ingest_for_user(user_id: int, days_ahead: int = 7) -> dict:
    """Scan upcoming events from both calendars and queue prep-todos.

    Returns {scanned: N, matched: N, added: N, mentioned: N}.
    """
    if not (GCAL_CALENDAR_ID_1 or GCAL_CALENDAR_ID_2):
        return {"scanned": 0, "matched": 0, "added": 0, "mentioned": 0, "skipped": "no calendars"}

    from app.services.gcal_client import get_events

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days_ahead)
    loop = asyncio.get_running_loop()
    events: list[dict] = []
    for cal_id in (GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2):
        if not cal_id:
            continue
        try:
            cal_events = await loop.run_in_executor(
                None, lambda cid=cal_id: get_events(cid, start=now, end=horizon, max_results=50)
            )
            events.extend(cal_events)
        except Exception as exc:
            logger.warning("gcal_ingest: read calendar %s failed: %s", cal_id, exc)

    matched = 0
    added = 0
    mentioned = 0

    for ev in events:
        title = (ev.get("summary") or "").strip()
        kw = _matches_prep(title)
        if not kw:
            continue
        matched += 1

        start_dt = _event_start(ev)
        if not start_dt:
            continue
        # Todo is due one day before the event
        due_date = (start_dt.astimezone().date() - timedelta(days=1)).isoformat()
        if due_date < now.date().isoformat():
            due_date = now.date().isoformat()  # don't queue a past prep todo

        text = _build_todo_text(ev, kw)

        existing = await todos_svc.mention_existing(user_id, text)
        if existing is not None:
            mentioned += 1
            continue

        await todos_svc.add_todo(user_id, text, source="gcal", due_date=due_date)
        added += 1

    logger.info(
        "gcal_ingest: user=%s scanned=%d matched=%d added=%d mentioned=%d",
        user_id, len(events), matched, added, mentioned,
    )
    return {
        "scanned": len(events),
        "matched": matched,
        "added": added,
        "mentioned": mentioned,
    }
