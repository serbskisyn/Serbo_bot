"""
evening_reflection.py — Day-end Telegram nudge with what's done / what's left.

Pushed at 21:30 (configurable). Pulls today's done todos and the still-open
ones, asks the user to mention anything not in the list. The completion
extractor (running on every chat) auto-marks anything the user reports
as done — so the conversation feels natural.

Also writes a markdown day-summary to app/data/summaries/{user_id}-{date}.md
so a future "weekly recap" feature has a corpus to chew on.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from app.bot import profile
from app.services import todos as todos_svc

logger = logging.getLogger(__name__)

_SUMMARY_DIR = Path(__file__).parent.parent / "data" / "summaries"

_WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _today_de(d: date | None = None) -> str:
    d = d or date.today()
    return f"{_WEEKDAYS_DE[d.weekday()]}, {d.strftime('%d.%m.%Y')}"


async def _todos_done_today(user_id: int) -> list[dict]:
    """All todos this user marked done since 00:00 today (Berlin)."""
    await todos_svc.init_db()
    cutoff = datetime.combine(date.today(), datetime.min.time()).astimezone(timezone.utc)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    async with aiosqlite.connect(todos_svc.TODOS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, text, source, updated_at
               FROM todos
               WHERE user_id = ? AND status = 'done' AND updated_at >= ?
               ORDER BY updated_at""",
            (user_id, cutoff_iso),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _decisions_logged_today(user_id: int) -> list[dict]:
    await todos_svc.init_db()
    cutoff = datetime.combine(date.today(), datetime.min.time()).astimezone(timezone.utc)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    async with aiosqlite.connect(todos_svc.TODOS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, text, notes
               FROM todos
               WHERE user_id = ? AND source = 'granola'
                 AND text LIKE 'Entscheidung:%'
                 AND created_at >= ?""",
            (user_id, cutoff_iso),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


def _source_badge(source: str) -> str:
    return {"chat": " 💬", "granola": " 🗣", "gcal": " 🗓"}.get(source, "")


async def assemble_evening_reflection(user_id: int) -> str:
    p = profile.get_profile(user_id)
    name = (p.get("identity") or {}).get("name") or ""

    lines = [f"🌙 *Tagesabschluss{f', {name}' if name else ''}*"]
    lines.append(f"_{_today_de()}_")

    done = await _todos_done_today(user_id)
    open_today_raw = await todos_svc.list_todos(user_id, scope="today")
    # Decisions live in todos table but get their own section — strip them from
    # the "offen" list to avoid showing the same line twice.
    open_today = [
        t for t in open_today_raw
        if not (t.get("text") or "").startswith("Entscheidung:")
    ]
    decisions = await _decisions_logged_today(user_id)

    if done:
        lines.append(f"\n✅ *Heute geschafft ({len(done)})*")
        for t in done[:8]:
            lines.append(f"• #{t['id']} {t['text']}{_source_badge(t.get('source', ''))}")
    else:
        lines.append("\n_Heute noch nichts erledigt — sicher?_")

    if open_today:
        lines.append(f"\n📌 *Noch offen ({len(open_today)})*")
        for t in open_today[:6]:
            badge = _source_badge(t.get("source", ""))
            lines.append(f"• #{t['id']} {t['text']}{badge}")
            ctx = todos_svc.parse_meeting_context(t.get("notes"))
            if ctx:
                lines.append(f"   ↳ _Meeting: {ctx[0]}_")

    if decisions:
        lines.append(f"\n💡 *Entscheidungen geloggt ({len(decisions)})*")
        groups: dict[str, list[str]] = {}
        ungrouped: list[str] = []
        for d in decisions[:8]:
            clean = d["text"].split("Entscheidung:", 1)[-1].strip()
            ctx = todos_svc.parse_meeting_context(d.get("notes"))
            if ctx:
                groups.setdefault(ctx[0], []).append(clean)
            else:
                ungrouped.append(clean)
        for meeting_title, items in groups.items():
            lines.append(f"\n*{meeting_title}*")
            for item in items:
                lines.append(f"• {item}")
        for item in ungrouped:
            lines.append(f"• {item}")

    lines.append(
        "\n_Hast du noch was geschafft, das nicht in der Liste war?_\n"
        "_Antworte einfach — ich check ab._"
    )

    return "\n".join(lines)


async def write_day_summary(user_id: int, body: str) -> Path:
    """Persist today's reflection text for later weekly-recap use."""
    today_iso = date.today().isoformat()
    _SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    path = _SUMMARY_DIR / f"{user_id}_{today_iso}_reflection.md"
    try:
        path.write_text(
            f"# Tagesabschluss {today_iso}\n\n{body}\n",
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("evening_reflection: summary write failed: %s", exc)
    return path
