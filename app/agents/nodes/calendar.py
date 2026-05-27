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
from app.bot.gcal_state import get_active_calendar
from app.config import GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

_BERLIN = ZoneInfo("Europe/Berlin")

_SYSTEM_PROMPT = (
    "Kalender-Assistent. Deutsch. Du bekommst die echten Kalendertermine des Users. "
    "Beantworte die Frage präzise auf Basis dieser Termine — Uhrzeiten nennen, "
    "kein Fülltext. Wenn die Frage z.B. nach freien Slots fragt, leite das aus den "
    "Terminen ab. Wenn keine Termine im relevanten Zeitraum sind, sag das klar. "
    "Erfinde KEINE Termine."
)


def _cal_id(cal_num: int) -> str:
    return GCAL_CALENDAR_ID_1 if cal_num == 1 else GCAL_CALENDAR_ID_2


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


def _fmt_event(ev: dict) -> str:
    summary = (ev.get("summary") or "(kein Titel)").strip()
    start = ev.get("start") or {}
    if "dateTime" in start:
        try:
            dt = datetime.fromisoformat(start["dateTime"]).astimezone(_BERLIN)
            return f"{dt.strftime('%a %d.%m. %H:%M')} — {summary}"
        except Exception:
            return f"? — {summary}"
    day = start.get("date", "")
    return f"{day} (ganztägig) — {summary}"


async def calendar_node(state: BotState) -> BotState:
    user_id = state["user_id"]
    text = state["text"]

    cal_num = get_active_calendar(user_id)
    cal_id = _cal_id(cal_num)
    if not cal_id:
        return {
            **state,
            "response": "❌ Kein Kalender konfiguriert (GCAL_CALENDAR_ID_1 fehlt).",
        }

    start, end, label = _window(text)
    logger.info("Calendar Node -> Fenster=%s | user=%d", label, user_id)

    try:
        from app.services.gcal_client import get_events
        loop = asyncio.get_running_loop()
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        events = await loop.run_in_executor(
            None, lambda: get_events(cal_id, start_utc, end_utc, 25)
        )
    except FileNotFoundError as exc:
        return {**state, "response": f"❌ Kalender nicht erreichbar: {exc}"}
    except Exception as exc:
        logger.warning("Calendar Node -> get_events failed: %s", exc)
        return {**state, "response": "❌ Konnte die Kalendertermine gerade nicht abrufen."}

    if not events:
        return {**state, "response": f"📅 Keine Termine für {label}."}

    event_lines = "\n".join(_fmt_event(e) for e in events)
    prompt = (
        f"Frage des Users: {text}\n\n"
        f"Kalendertermine ({label}):\n{event_lines}\n\n"
        f"Beantworte die Frage auf Basis dieser Termine."
    )
    response = await ask_llm(prompt, history=state.get("messages", []), system_prompt=_SYSTEM_PROMPT)
    logger.info("Calendar Node -> Antwort generiert | user=%d | %d Termine", user_id, len(events))
    return {**state, "response": response}
