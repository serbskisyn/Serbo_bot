"""
granola_sync.py — Orchestrate Granola pull → todos + profile.people.

For each meeting returned by granola_lookup:
  • each commitment   → todos.add_todo (source="granola", due=meeting_date+3d)
                        — or mention_existing if we already track it
  • each mentioned_person → profile.add_dict_item("people", {...})
  • each decision       → todos.add_todo as a low-priority "context" note
                        marked with notes="decision from <meeting>"

Idempotent: re-running on the same Granola payload only bumps mention
counts, never duplicates rows.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from app.bot import profile
from app.services import granola_lookup, todos as todos_svc

logger = logging.getLogger(__name__)


def _parse_meeting_date(s: str) -> date:
    """Best-effort meeting-date parse. Falls back to today on failure."""
    s = (s or "").strip()
    if not s:
        return date.today()
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        try:
            return date.fromisoformat(s)
        except ValueError:
            return date.today()


async def sync_for_user(user_id: int, lookback_hours: int = 30) -> dict:
    """Pull Granola, persist into todos + profile.

    The user's name from profile.identity is passed into the lookup so the
    LLM filters commitments to only those that the user personally owns.

    Returns counters: {meetings, commitments_added, commitments_mentioned,
                       decisions_added, people_added, error}.
    """
    identity = profile.get_section(user_id, "identity") or {}
    user_name = (identity.get("name") or "").strip()
    if not user_name:
        logger.warning(
            "granola_sync: no identity.name for user=%s — falling back to "
            "unfiltered extraction. Set the name with /memory or chat.",
            user_id,
        )

    result = await granola_lookup.get_recent_meetings(
        lookback_hours=lookback_hours,
        user_name=user_name,
    )
    if result.get("error"):
        return {
            "meetings": 0, "commitments_added": 0, "commitments_mentioned": 0,
            "decisions_added": 0, "people_added": 0, "error": result["error"],
        }

    meetings = result.get("meetings", [])
    today = date.today()

    counters = {
        "meetings": len(meetings),
        "commitments_added": 0,
        "commitments_mentioned": 0,
        "decisions_added": 0,
        "people_added": 0,
        "error": None,
    }

    for m in meetings:
        title = m.get("title") or "(meeting)"
        meeting_d = _parse_meeting_date(m.get("date") or "")
        # Commitments default to "due 3 days after the meeting" — long enough
        # that this morning's notes don't all collide with today, short enough
        # to still be actionable.
        default_due = max(today, meeting_d + timedelta(days=3)).isoformat()

        for c in m.get("commitments") or []:
            existing = await todos_svc.mention_existing(user_id, c)
            if existing is not None:
                counters["commitments_mentioned"] += 1
                continue
            await todos_svc.add_todo(
                user_id, c,
                source="granola",
                due_date=default_due,
                notes=f"aus Meeting: {title} ({meeting_d.isoformat()})",
            )
            counters["commitments_added"] += 1

        for d in m.get("decisions") or []:
            note_text = f"Entscheidung: {d}"
            existing = await todos_svc.mention_existing(user_id, note_text)
            if existing is not None:
                continue
            await todos_svc.add_todo(
                user_id, note_text,
                source="granola",
                due_date=None,  # decisions don't have a due date — they're context
                notes=f"aus Meeting: {title} ({meeting_d.isoformat()})",
            )
            counters["decisions_added"] += 1

        for p in m.get("mentioned_people") or []:
            await profile.add_dict_item(user_id, "people", {
                "name": p,
                "last_mentioned": meeting_d.isoformat(),
                "notes": f"erwähnt in {title}",
            })
            counters["people_added"] += 1

    logger.info(
        "granola_sync: user=%s meetings=%d commits=+%d/%d↑ decisions=+%d people=+%d",
        user_id, counters["meetings"],
        counters["commitments_added"], counters["commitments_mentioned"],
        counters["decisions_added"], counters["people_added"],
    )
    return counters
