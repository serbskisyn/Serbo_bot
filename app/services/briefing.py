"""
briefing.py — Assemble the morning briefing for one user.

Pulls from four data sources we already maintain:

  • Google Calendar (today's events from both configured calendars)
  • todos.db          (top open todos by priority)
  • todos.db with src=granola + text starting "Entscheidung:" — yesterday's decisions
  • profile.yaml `people` section — relationship alerts (>N days unmentioned)

Returns a formatted Markdown string ready for Telegram. No network calls
beyond the calendar read; the Granola data is already in todos (sync runs
at 06:15, briefing at 07:30).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.bot import profile
from app.config import (
    BRIEFING_RELATIONSHIP_ALERT_DAYS, BRIEFING_TOP_TODOS,
    GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2,
)
from app.services import todos as todos_svc

logger = logging.getLogger(__name__)

_BERLIN = ZoneInfo("Europe/Berlin")

_WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _today_de(d: date | None = None) -> str:
    d = d or date.today()
    return f"{_WEEKDAYS_DE[d.weekday()]}, {d.strftime('%d.%m.%Y')}"


def _fmt_due_short(due: str | None) -> str:
    if not due:
        return ""
    try:
        d = date.fromisoformat(due)
        today = date.today()
        delta = (d - today).days
        if delta < 0:
            return f"⚠️ überfällig ({-delta}d)"
        if delta == 0:
            return "heute"
        if delta == 1:
            return "morgen"
        if delta <= 7:
            return f"in {delta}d"
        return d.strftime("%d.%m.")
    except Exception:
        return ""


def _source_badge(source: str) -> str:
    return {"chat": " 💬", "granola": " 🗣", "gcal": " 🗓"}.get(source, "")


async def _fetch_today_events() -> list[dict]:
    """Pull today's events from both configured calendars."""
    if not (GCAL_CALENDAR_ID_1 or GCAL_CALENDAR_ID_2):
        return []
    from app.services.gcal_client import get_events

    now_berlin = datetime.now(tz=_BERLIN)
    start_of_day = datetime.combine(now_berlin.date(), time(0, 0), tzinfo=_BERLIN)
    end_of_day = start_of_day + timedelta(days=1)

    loop = asyncio.get_running_loop()
    events: list[dict] = []
    for cal_id in (GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2):
        if not cal_id:
            continue
        try:
            evs = await loop.run_in_executor(
                None, lambda cid=cal_id: get_events(cid, start=start_of_day, end=end_of_day, max_results=20)
            )
            events.extend(evs)
        except Exception as exc:
            logger.warning("briefing: get_events(%s) failed: %s", cal_id, exc)
    # Sort by start time, all-day events first
    events.sort(key=lambda e: (
        "dateTime" in (e.get("start") or {}),
        (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date", ""),
    ))
    return events


def _format_event_line(ev: dict) -> str:
    title = (ev.get("summary") or "(kein Titel)").strip()
    start = ev.get("start") or {}
    if "dateTime" in start:
        try:
            dt = datetime.fromisoformat(start["dateTime"]).astimezone(_BERLIN)
            return f"• {dt.strftime('%H:%M')} — {title}"
        except Exception:
            return f"• {title}"
    return f"• 🗓 ganztägig — {title}"


def _relationship_alerts(user_id: int, threshold_days: int) -> list[tuple[str, int]]:
    """Return (name, days_unmentioned) pairs over the threshold, oldest first."""
    people = profile.get_section(user_id, "people") or []
    if not people:
        return []
    today = date.today()
    alerts: list[tuple[str, int]] = []
    for p in people:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        last_seen = (p.get("last_mentioned") or "").strip()
        if not name or not last_seen:
            continue
        try:
            last_d = date.fromisoformat(last_seen[:10])
        except ValueError:
            continue
        days = (today - last_d).days
        if days >= threshold_days:
            alerts.append((name, days))
    alerts.sort(key=lambda t: t[1], reverse=True)
    return alerts[:5]


async def _yesterday_decisions(user_id: int) -> list[str]:
    """Granola-sourced decisions added in the last ~36h."""
    await todos_svc.init_db()
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    import aiosqlite
    async with aiosqlite.connect(todos_svc.TODOS_DB) as db:
        async with db.execute(
            """SELECT text, notes
               FROM todos
               WHERE user_id = ?
                 AND source = 'granola'
                 AND text LIKE 'Entscheidung:%'
                 AND created_at >= ?""",
            (user_id, cutoff),
        ) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows][:5]


async def assemble_briefing(user_id: int) -> str:
    """Build the morning briefing for one user. Returns Markdown string."""
    # Identity for the greeting
    p = profile.get_profile(user_id)
    name = (p.get("identity") or {}).get("name") or ""

    greeting = f"🌅 *Guten Morgen{f', {name}' if name else ''}!*"
    date_line = f"_{_today_de()}_"

    # Today's events
    events = await _fetch_today_events()
    if events:
        ev_lines = [f"\n📅 *Heute ({len(events)} Termine)*"]
        ev_lines.extend(_format_event_line(ev) for ev in events[:10])
        events_block = "\n".join(ev_lines)
    else:
        events_block = "\n📅 *Heute keine Termine*"

    # Top todos
    today_todos = await todos_svc.list_todos(user_id, scope="today")
    if today_todos:
        todo_lines = [f"\n✅ *Top Todos ({len(today_todos)} offen)*"]
        for t in today_todos[:BRIEFING_TOP_TODOS]:
            id_s = f"*#{t['id']}*"
            due_s = _fmt_due_short(t.get("due_date"))
            due_part = f" — {due_s}" if due_s else ""
            badge = _source_badge(t.get("source", ""))
            mentions = int(t.get("mention_count") or 1)
            heat = " 🔥" if mentions >= 3 else ""
            todo_lines.append(f"• {id_s} {t['text']}{due_part}{badge}{heat}")
        todos_block = "\n".join(todo_lines)
    else:
        todos_block = "\n✅ *Keine offenen Todos — Glückwunsch!*"

    # Yesterday's decisions
    decisions = await _yesterday_decisions(user_id)
    if decisions:
        dec_lines = ["\n💡 *Aus gestrigen Meetings*"]
        for d in decisions:
            # Strip the "Entscheidung: " prefix for cleaner display
            text = d.split("Entscheidung:", 1)[-1].strip()
            dec_lines.append(f"• {text}")
        decisions_block = "\n".join(dec_lines)
    else:
        decisions_block = ""

    # Relationship alerts
    alerts = _relationship_alerts(user_id, BRIEFING_RELATIONSHIP_ALERT_DAYS)
    if alerts:
        alert_lines = ["\n🤝 *Lange nichts gehört von*"]
        for name_, days in alerts:
            alert_lines.append(f"• {name_} ({days} Tage)")
        alerts_block = "\n".join(alert_lines)
    else:
        alerts_block = ""

    parts = [greeting, date_line, events_block, todos_block]
    if decisions_block:
        parts.append(decisions_block)
    if alerts_block:
        parts.append(alerts_block)
    return "\n".join(parts)
