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

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite

from app.bot import profile
from app.config import GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2
from app.services import todos as todos_svc

logger = logging.getLogger(__name__)

_SUMMARY_DIR = Path(__file__).parent.parent / "data" / "summaries"
_BERLIN = ZoneInfo("Europe/Berlin")

# Calendar slot labels — keep in sync with app/agents/nodes/calendar.py
_CAL_LABELS = ("Benno@atolls.com", "Bennoschwede@gmail.com")

_WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


async def _fetch_tomorrow_events() -> list[dict]:
    """Tomorrow's events from both configured calendars, tagged by source."""
    if not (GCAL_CALENDAR_ID_1 or GCAL_CALENDAR_ID_2):
        return []
    from app.services.gcal_client import get_events

    now = datetime.now(tz=_BERLIN)
    start = datetime.combine(now.date(), time(0, 0), tzinfo=_BERLIN) + timedelta(days=1)
    end = start + timedelta(days=1)

    loop = asyncio.get_running_loop()
    events: list[dict] = []
    for cal_id, cal_label in zip((GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2), _CAL_LABELS):
        if not cal_id:
            continue
        try:
            evs = await loop.run_in_executor(
                None, lambda cid=cal_id: get_events(cid, start=start, end=end, max_results=20)
            )
            for e in evs:
                e["_cal_label"] = cal_label
            events.extend(evs)
        except Exception as exc:
            logger.warning("reflection: get_events(%s) failed: %s", cal_label, exc)
    events.sort(key=lambda e: (
        "dateTime" in (e.get("start") or {}),
        (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date", ""),
    ))
    return events


def _fmt_cal_event(ev: dict) -> str:
    title = (ev.get("summary") or "(kein Titel)").strip()
    cal_label = ev.get("_cal_label", "")
    tag = f" [{cal_label}]" if cal_label else ""
    start = ev.get("start") or {}
    if "dateTime" in start:
        try:
            dt = datetime.fromisoformat(start["dateTime"]).astimezone(_BERLIN)
            return f"• {dt.strftime('%H:%M')} — {title}{tag}"
        except Exception:
            return f"• {title}{tag}"
    return f"• 🗓 ganztägig — {title}{tag}"


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

    # Look-ahead: tomorrow's calendar (both calendars, source-tagged)
    tomorrow_events = await _fetch_tomorrow_events()
    if tomorrow_events:
        tmrw = (date.today() + timedelta(days=1))
        lines.append(f"\n📅 *Morgen ({_WEEKDAYS_DE[tmrw.weekday()]}, {tmrw.strftime('%d.%m.')})*")
        for ev in tomorrow_events[:10]:
            lines.append(_fmt_cal_event(ev))

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
    # Make the reflection recallable (fire-and-forget; never block the write)
    try:
        from app.services.notes_index import index_file
        await index_file(path)
    except Exception as exc:
        logger.debug("evening_reflection: index_file skipped: %s", exc)
    return path
