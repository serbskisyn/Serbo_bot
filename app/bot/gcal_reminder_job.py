import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import (
    GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2,
    GCAL_REMINDER_MINUTES, GCAL_CHECK_INTERVAL_MINUTES,
    ALLOWED_USER_IDS,
)
from app.services.gcal_client import get_events, format_event
from app.bot.bot_context import get_bot

_BERLIN = ZoneInfo("Europe/Berlin")
_WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni",
               "Juli", "August", "September", "Oktober", "November", "Dezember"]

logger = logging.getLogger(__name__)

_NOTIFIED_PATH = Path("app/data/gcal_notified.json")

# Tracks already-notified events: "{event_id}_{date}" — persisted across restarts
_notified: set[str] = set()


def _load_notified() -> None:
    """Load persisted notified-set from disk; drop entries older than yesterday."""
    global _notified
    if not _NOTIFIED_PATH.exists():
        return
    try:
        data: list[str] = json.loads(_NOTIFIED_PATH.read_text())
        today = datetime.now(_BERLIN).date()
        yesterday = (today - timedelta(days=1)).isoformat()
        # Keep only today's and yesterday's entries (avoids unbounded growth)
        _notified = {k for k in data if k.endswith(today.isoformat()) or k.endswith(yesterday)}
        logger.info("gcal_reminder: %d gespeicherte Erinnerungen geladen", len(_notified))
    except Exception as e:
        logger.warning("gcal_reminder: Notified-Cache konnte nicht geladen werden: %s", e)


def _save_notified() -> None:
    """Persist current notified-set to disk."""
    try:
        _NOTIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
        _NOTIFIED_PATH.write_text(json.dumps(sorted(_notified)))
    except Exception as e:
        logger.warning("gcal_reminder: Notified-Cache konnte nicht gespeichert werden: %s", e)


_load_notified()


async def _check_and_notify(context) -> None:
    bot = get_bot()
    if not bot:
        return

    now = datetime.now(timezone.utc)
    window_start = now + timedelta(minutes=GCAL_REMINDER_MINUTES - 1)
    window_end = now + timedelta(minutes=GCAL_REMINDER_MINUTES + GCAL_CHECK_INTERVAL_MINUTES)

    calendars: list[tuple[str, str]] = []
    if GCAL_CALENDAR_ID_1:
        calendars.append((GCAL_CALENDAR_ID_1, "Kalender 1"))
    if GCAL_CALENDAR_ID_2:
        calendars.append((GCAL_CALENDAR_ID_2, "Kalender 2"))

    if not calendars:
        return

    loop = asyncio.get_running_loop()
    for calendar_id, label in calendars:
        try:
            events = await loop.run_in_executor(
                None, get_events, calendar_id, window_start, window_end
            )
        except Exception as e:
            logger.warning("Reminder fetch fehlgeschlagen (%s): %s", label, e)
            continue

        for event in events:
            # Skip all-day events — no specific start time, reminder doesn't apply
            start = event.get('start', {})
            if 'dateTime' not in start:
                continue

            # Skip events that have already started or are in the past
            event_start = datetime.fromisoformat(start['dateTime']).astimezone(timezone.utc)
            if event_start <= now:
                continue

            event_id = event.get('id', '')
            key = f"{event_id}_{now.date()}"
            if key in _notified:
                continue
            _notified.add(key)
            _save_notified()

            line = format_event(event)
            text = (
                f"⏰ *Erinnerung in {GCAL_REMINDER_MINUTES} Min.*\n\n"
                f"{line}\n\n"
                f"_{label}_"
            )
            for user_id in ALLOWED_USER_IDS:
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode="Markdown",
                    )
                    logger.info("Kalender-Erinnerung gesendet | user=%d | %s", user_id, event.get('summary', '?'))
                except Exception as e:
                    logger.warning("Reminder senden fehlgeschlagen | user=%d: %s", user_id, e)


async def send_daily_calendar_summary(context) -> None:
    bot = get_bot()
    if not bot:
        return

    now = datetime.now(_BERLIN)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    day_end = (day_start + timedelta(days=1))

    dow = _WEEKDAYS_DE[now.weekday()]
    date_str = f"{dow}, {now.day}. {_MONTHS_DE[now.month - 1]} {now.year}"

    calendars = []
    if GCAL_CALENDAR_ID_1:
        calendars.append((GCAL_CALENDAR_ID_1, "Atolls (Arbeit)"))
    if GCAL_CALENDAR_ID_2:
        calendars.append((GCAL_CALENDAR_ID_2, "Gmail"))

    if not calendars:
        return

    loop = asyncio.get_running_loop()
    blocks = []
    total_events = 0

    for calendar_id, label in calendars:
        try:
            events = await loop.run_in_executor(
                None, get_events, calendar_id, day_start, day_end
            )
        except Exception as e:
            logger.warning("Tageszusammenfassung fetch fehlgeschlagen (%s): %s", label, e)
            continue

        if events:
            total_events += len(events)
            lines = [f"*{label}*"]
            for e in events:
                summary = e.get('summary', '(kein Titel)').replace('_', '\\_').replace('*', '\\*')
                start = e.get('start', {})
                if 'dateTime' in start:
                    dt = datetime.fromisoformat(start['dateTime']).astimezone(_BERLIN)
                    time_prefix = f"🕐 {dt.strftime('%H:%M')} "
                else:
                    time_prefix = "🗓 "
                lines.append(f"{time_prefix}{summary}")
            blocks.append("\n".join(lines))
        else:
            blocks.append(f"*{label}*\n_(keine Termine)_")

    if total_events == 0:
        text = f"📅 *{date_str}*\n\n_Heute keine Termine_ ☀️"
    else:
        text = f"📅 *Guten Morgen\\!* {date_str}\n\n" + "\n\n".join(blocks)

    for user_id in ALLOWED_USER_IDS:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info("Tages-Kalenderübersicht gesendet | user=%d | %d Termine", user_id, total_events)
        except Exception as e:
            logger.warning("Tages-Kalenderübersicht fehlgeschlagen | user=%d: %s", user_id, e)


def register_gcal_reminder_job(app) -> None:
    if app.job_queue is None:
        logger.warning("JobQueue nicht verfügbar — Kalender-Erinnerungen deaktiviert.")
        return

    if not GCAL_CALENDAR_ID_1 and not GCAL_CALENDAR_ID_2:
        logger.info("Keine GCAL_CALENDAR_ID_* gesetzt — Kalender-Erinnerungen deaktiviert.")
        return

    app.job_queue.run_repeating(
        callback=_check_and_notify,
        interval=GCAL_CHECK_INTERVAL_MINUTES * 60,
        first=30,
        name="gcal_reminder",
    )
    logger.info(
        "Kalender-Erinnerungen aktiv: alle %d Min., %d Min. vor Termin",
        GCAL_CHECK_INTERVAL_MINUTES, GCAL_REMINDER_MINUTES,
    )
