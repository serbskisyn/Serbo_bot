"""
proactive_context.py — surfaces what the user has on their plate into the
chat prompt, so the bot can bring up relevant/overdue items mid-conversation
instead of only in scheduled pushes.

Aggregates two sources, hard items first:
  • open todos due today/overdue (todos.db) — committed, actionable
  • soft pending context items (context.db) — mention-weighted entities/intents

Keyword-deduplicated and capped at PROACTIVE_MAX_ITEMS, rendered as a German
context block. Fail-safe: always returns a string, never raises.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from app.config import PROACTIVE_CONTEXT_ENABLED, PROACTIVE_MAX_ITEMS

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "der", "die", "das", "den", "dem", "ein", "eine", "und", "oder", "mit",
    "für", "von", "zu", "zum", "zur", "im", "in", "an", "auf", "noch", "nicht",
    "vorbereiten", "machen", "erledigen", "the", "for", "and",
}


def _keywords(text: str) -> set[str]:
    return {w for w in (text or "").lower().split() if len(w) > 3 and w not in _STOPWORDS}


def _due_marker(due: str | None, today: date) -> str:
    if not due:
        return ""
    try:
        d = datetime.strptime(due[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return ""
    days = (d - today).days
    if days < 0:
        return f" [ÜBERFÄLLIG seit {abs(days)}d]"
    if days == 0:
        return " [HEUTE fällig]"
    if days <= 3:
        return f" [in {days}d fällig]"
    return f" [fällig {due[:10]}]"


async def get_proactive_context(user_id: int) -> str:
    if not PROACTIVE_CONTEXT_ENABLED:
        return ""

    today = datetime.now(tz=timezone.utc).date()
    lines: list[str] = []
    seen_kw: list[set[str]] = []

    def _accept(label: str) -> bool:
        kw = _keywords(label)
        if kw and any(kw & s for s in seen_kw):
            return False
        seen_kw.append(kw or {label.lower()})
        return True

    # 1) Hard todos — today/overdue, highest signal first
    try:
        from app.services import todos as todos_svc
        rows = await todos_svc.list_todos(user_id, scope="today")
        for t in rows:
            if len(lines) >= PROACTIVE_MAX_ITEMS:
                break
            text = t.get("text", "")
            if not _accept(text):
                continue
            lines.append(f"- ✅ {text}{_due_marker(t.get('due_date'), today)}")
    except Exception as exc:
        logger.debug("proactive: todos collect failed: %s", exc)

    # 2) Soft pending — mention-weighted entities/intents
    try:
        from app.services import context_store
        items = await context_store.get_pending_items(user_id, limit=PROACTIVE_MAX_ITEMS)
        for it in items:
            if len(lines) >= PROACTIVE_MAX_ITEMS:
                break
            name = it.get("name", "")
            if not _accept(name):
                continue
            etype = it.get("entity_type", "")
            mentions = it.get("mention_count", 1)
            mtag = f", {mentions}× erwähnt" if mentions > 1 else ""
            lines.append(f"- ◦ [{etype}] {name}{_due_marker(it.get('due_date'), today)}{mtag}")
    except Exception as exc:
        logger.debug("proactive: soft collect failed: %s", exc)

    if not lines:
        return ""
    return (
        "\nOffene Themen des Users (nur erwähnen wenn zum Gespräch passend, "
        "nicht stur auflisten):\n" + "\n".join(lines)
    )
