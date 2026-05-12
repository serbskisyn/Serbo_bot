"""
lead_qualifying_agent.py — Scheduler entry point for the Lead Qualifying Agent.

Registers a daily job in the Telegram JobQueue that runs the full lead
qualifying pipeline every 24 hours.

Usage (in main.py _post_init):
    from app.agents.schedule.lead_qualifying_agent import register_lead_qualifying_job
    register_lead_qualifying_job(application)
"""
from __future__ import annotations

import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram.ext import Application

logger = logging.getLogger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")

# Default run time: 08:00 Europe/Berlin
# Override via LEAD_QUALIFYING_HOUR / LEAD_QUALIFYING_MINUTE env vars.
_DEFAULT_HOUR = 8
_DEFAULT_MINUTE = 0


def _get_schedule_time() -> time:
    import os
    try:
        hour = int(os.getenv("LEAD_QUALIFYING_HOUR", str(_DEFAULT_HOUR)))
    except ValueError:
        hour = _DEFAULT_HOUR
    try:
        minute = int(os.getenv("LEAD_QUALIFYING_MINUTE", str(_DEFAULT_MINUTE)))
    except ValueError:
        minute = _DEFAULT_MINUTE
    return time(hour=hour, minute=minute, second=0, tzinfo=BERLIN)


async def _run_lead_qualifying_job(context) -> None:  # noqa: ANN001
    """JobQueue callback: run the lead qualifying pipeline."""
    logger.info("Lead-Qualifying-Job gestartet")
    try:
        from app.agents.lead_qualifying.graph import run_pipeline
        final_state = await run_pipeline()
        processed_count = len(final_state.get("processed_leads", []))
        errors = final_state.get("errors", [])
        logger.info(
            "Lead-Qualifying-Job beendet: %d Leads verarbeitet, %d Fehler",
            processed_count, len(errors),
        )
    except Exception as exc:
        logger.error("Lead-Qualifying-Job fehlgeschlagen: %s", exc, exc_info=True)

        # Notify admin on hard failure
        try:
            from app.bot.bot_context import get_bot
            from app.config import ADMIN_CHAT_ID

            bot = get_bot()
            if bot and ADMIN_CHAT_ID:
                await bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"*Lead-Qualifying-Job fehlgeschlagen*\n\nFehler: `{exc}`",
                    parse_mode="Markdown",
                )
        except Exception as notify_exc:
            logger.warning("Fehler-Benachrichtigung konnte nicht gesendet werden: %s", notify_exc)


def register_lead_qualifying_job(app: Application) -> None:
    """
    Register the lead qualifying pipeline as a daily JobQueue job.

    Call this from main.py inside _post_init after the bot is ready.
    Runs at LEAD_QUALIFYING_HOUR:LEAD_QUALIFYING_MINUTE Europe/Berlin (default 08:00).
    """
    if app.job_queue is None:
        logger.warning(
            "JobQueue nicht verfügbar — Lead-Qualifying-Job nicht registriert. "
            "Bitte 'python-telegram-bot[job-queue]' installieren."
        )
        return

    run_time = _get_schedule_time()
    app.job_queue.run_daily(
        callback=_run_lead_qualifying_job,
        time=run_time,
        name="lead_qualifying_daily",
    )
    logger.info(
        "Lead-Qualifying-Job registriert: täglich %02d:%02d Europe/Berlin",
        run_time.hour,
        run_time.minute,
    )
