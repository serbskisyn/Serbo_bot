import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import (
    GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2,
    GCAL_REMINDER_MINUTES, GCAL_CHECK_INTERVAL_MINUTES,
    ALLOWED_USER_IDS,
)
from app.services.gcal_client import get_events, format_event
from app.bot.bot_context import get_bot

logger = logging.getLogger(__name__)

# Tracks already-notified events: "{event_id}_{date}" to avoid duplicate pushes
_notified: set[str] = set()


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

    loop = asyncio.get_event_loop()
    for calendar_id, label in calendars:
        try:
            events = await loop.run_in_executor(
                None, get_events, calendar_id, window_start, window_end
            )
        except Exception as e:
            logger.warning("Reminder fetch fehlgeschlagen (%s): %s", label, e)
            continue

        for event in events:
            event_id = event.get('id', '')
            key = f"{event_id}_{now.date()}"
            if key in _notified:
                continue
            _notified.add(key)

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
