"""
calendar.py — LangGraph node: answer calendar questions in normal chat.

Previously calendar data was only reachable via the /termine command;
asking "habe ich heute noch Termine?" in chat hit the general node which
has no calendar access and replied that it didn't know.

This node:
  1. picks the active calendar (gcal_state, same as /termine)
  2. derives a time window from the query (heute / morgen / übermorgen /
     woche — default: today + next 3 days for open-ended questions)
  3. fetches events via gcal_client.get_events
  4. lets the LLM answer the user's actual question grounded on the events
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.agents.state import BotState
from app.config import GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

_BERLIN = ZoneInfo("Europe/Berlin")

_SYSTEM_PROMPT = (
    "Kalender-Assistent. Deutsch. Du bekommst die echten Kalendertermine des Users "
    "aus allen seinen Kalendern. "
    "Beantworte die Frage präzise auf Basis dieser Termine — Uhrzeiten nennen, "
    "kein Fülltext. Wenn die Frage z.B. nach freien Slots fragt, leite das aus den "
    "Terminen ab. Wenn keine Termine im relevanten Zeitraum sind, sag das klar. "
    "Erfinde KEINE Termine."
)


def _configured_calendars() -> list[tuple[str, str]]:
    """Return [(calendar_id, label), ...] for every configured calendar."""
    cals = []
    if GCAL_CALENDAR_ID_1:
        cals.append((GCAL_CALENDAR_ID_1, "Benno@atolls.com"))
    if GCAL_CALENDAR_ID_2:
        cals.append((GCAL_CALENDAR_ID_2, "Bennoschwede@gmail.com"))
    return cals


def _window(text: str) -> tuple[datetime, datetime, str]:
    """Derive (start, end, label) in Berlin tz from the query wording."""
    low = text.lower()
    now = datetime.now(_BERLIN)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if "übermorgen" in low or "uebermorgen" in low:
        start = midnight + timedelta(days=2)
        return start, start + timedelta(days=1), "übermorgen"
    if "morgen" in low:
        start = midnight + timedelta(days=1)
        return start, start + timedelta(days=1), "morgen"
    if "woche" in low or "nächste" in low or "naechste" in low:
        return midnight, midnight + timedelta(days=7), "diese Woche"
    if "heute" in low or "jetzt" in low or "noch" in low:
        return midnight, midnight + timedelta(days=1), "heute"
    # Open-ended ("was steht an?", "meine Termine") → today + 3 days
    return midnight, midnight + timedelta(days=3), "die nächsten Tage"


def _event_sort_key(ev: dict) -> str:
    start = ev.get("start") or {}
    return start.get("dateTime") or start.get("date") or ""


def _fmt_event(ev: dict) -> str:
    summary = (ev.get("summary") or "(kein Titel)").strip()
    cal_label = ev.get("_cal_label", "")
    cal_tag = f" [{cal_label}]" if cal_label else ""
    start = ev.get("start") or {}
    if "dateTime" in start:
        try:
            dt = datetime.fromisoformat(start["dateTime"]).astimezone(_BERLIN)
            return f"{dt.strftime('%a %d.%m. %H:%M')} — {summary}{cal_tag}"
        except Exception:
            return f"? — {summary}{cal_tag}"
    day = start.get("date", "")
    return f"{day} (ganztägig) — {summary}{cal_tag}"


async def calendar_node(state: BotState) -> BotState:
    user_id = state["user_id"]
    text = state["text"]

    calendars = _configured_calendars()
    if not calendars:
        return {
            **state,
            "response": "❌ Kein Kalender konfiguriert (GCAL_CALENDAR_ID_1 / _2 fehlen).",
        }

    start, end, label = _window(text)
    logger.info("Calendar Node -> Fenster=%s | %d Kalender | user=%d", label, len(calendars), user_id)

    try:
        from app.services.gcal_client import get_events
        loop = asyncio.get_running_loop()
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)

        events: list[dict] = []
        errors = 0
        for cal_id, cal_label in calendars:
            try:
                evs = await loop.run_in_executor(
                    None, lambda cid=cal_id: get_events(cid, start_utc, end_utc, 25)
                )
                for e in evs:
                    e["_cal_label"] = cal_label
                events.extend(evs)
            except Exception as exc:
                errors += 1
                logger.warning("Calendar Node -> get_events(%s) failed: %s", cal_label, exc)
    except FileNotFoundError as exc:
        return {**state, "response": f"❌ Kalender nicht erreichbar: {exc}"}

    if not events:
        if errors == len(calendars):
            return {**state, "response": "❌ Konnte die Kalendertermine gerade nicht abrufen."}
        return {**state, "response": f"📅 Keine Termine für {label}."}

    events.sort(key=_event_sort_key)
    event_lines = "\n".join(_fmt_event(e) for e in events)
    prompt = (
        f"Frage des Users: {text}\n\n"
        f"Kalendertermine ({label}, alle Kalender):\n{event_lines}\n\n"
        f"Beantworte die Frage auf Basis dieser Termine."
    )
    response = await ask_llm(prompt, history=state.get("messages", []), system_prompt=_SYSTEM_PROMPT)
    logger.info("Calendar Node -> Antwort generiert | user=%d | %d Termine", user_id, len(events))
    return {**state, "response": response}
