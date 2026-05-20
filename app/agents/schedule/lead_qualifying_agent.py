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

# 2× täglich (Morgen + Nachmittag). Defaults überschreibbar via Env-Vars:
#   LEAD_QUALIFYING_TIMES="08:00,16:00"   (komma-separierte HH:MM-Liste)
#   LEAD_QUALIFYING_HOUR / LEAD_QUALIFYING_MINUTE bleiben Backwards-Compat (1× täglich).
_DEFAULT_TIMES = "08:00,16:00"


def _parse_time(spec: str) -> time | None:
    try:
        hh, mm = spec.strip().split(":", 1)
        return time(hour=int(hh), minute=int(mm), second=0, tzinfo=BERLIN)
    except (ValueError, AttributeError):
        return None


def _get_schedule_times() -> list[time]:
    """Liste täglicher Run-Zeiten in Europe/Berlin.

    Bevorzugt LEAD_QUALIFYING_TIMES (komma-separiert); fällt auf legacy
    LEAD_QUALIFYING_HOUR/MINUTE zurück (1×); sonst Default 08:00 + 16:00.
    """
    import os
    raw = os.getenv("LEAD_QUALIFYING_TIMES", "").strip()
    if raw:
        slots = [t for spec in raw.split(",") if (t := _parse_time(spec))]
        if slots:
            return slots

    legacy_hour = os.getenv("LEAD_QUALIFYING_HOUR")
    if legacy_hour is not None:
        try:
            return [time(
                hour=int(legacy_hour),
                minute=int(os.getenv("LEAD_QUALIFYING_MINUTE", "0")),
                second=0, tzinfo=BERLIN,
            )]
        except ValueError:
            pass

    return [t for spec in _DEFAULT_TIMES.split(",") if (t := _parse_time(spec))]


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
    Register Lead-Qualifying als JobQueue-Job für jeden konfigurierten Time-Slot.

    Default: 2× täglich (08:00 + 16:00 Europe/Berlin).
    Override via LEAD_QUALIFYING_TIMES="08:00,16:00,..." (komma-separierte HH:MM-Liste).
    """
    if app.job_queue is None:
        logger.warning(
            "JobQueue nicht verfügbar — Lead-Qualifying-Job nicht registriert. "
            "Bitte 'python-telegram-bot[job-queue]' installieren."
        )
        return

    slots = _get_schedule_times()
    if not slots:
        logger.warning("Keine gültigen Run-Zeiten für Lead-Qualifying — Job nicht registriert")
        return

    for run_time in slots:
        slot_label = f"{run_time.hour:02d}{run_time.minute:02d}"
        app.job_queue.run_daily(
            callback=_run_lead_qualifying_job,
            time=run_time,
            name=f"lead_qualifying_daily_{slot_label}",
        )
    logger.info(
        "Lead-Qualifying registriert: %d Slot(s) Europe/Berlin — %s",
        len(slots),
        ", ".join(f"{t.hour:02d}:{t.minute:02d}" for t in slots),
    )
