"""
sync_jobs.py — Scheduler entry-points for Granola + Google Calendar ingest.

Both jobs iterate over NEWS_DAILY_PUSH_USER_IDS (the same whitelist the
news-push uses) so each receiver gets their own todo backlog filled.

Granola pull runs once a day in the early morning (last 30h window so
nothing falls through a Friday → Monday gap).

GCal pull runs every 6 hours — calendars change more often than meetings
get added, and one ingest is cheap (~1s, no LLM).
"""
from __future__ import annotations

import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram.ext import Application

from app.config import NEWS_DAILY_PUSH_USER_IDS
from app.services import granola_sync, gcal_ingest

logger = logging.getLogger(__name__)

_BERLIN = ZoneInfo("Europe/Berlin")


async def _granola_daily_pull(context) -> None:
    if not NEWS_DAILY_PUSH_USER_IDS:
        logger.info("granola_daily_pull: no users configured")
        return
    for user_id in NEWS_DAILY_PUSH_USER_IDS:
        try:
            await granola_sync.sync_for_user(user_id, lookback_hours=30)
        except Exception as exc:
            logger.warning("granola_daily_pull: user=%s failed: %s", user_id, exc)


async def _gcal_ingest_periodic(context) -> None:
    if not NEWS_DAILY_PUSH_USER_IDS:
        return
    for user_id in NEWS_DAILY_PUSH_USER_IDS:
        try:
            await gcal_ingest.ingest_for_user(user_id, days_ahead=7)
        except Exception as exc:
            logger.warning("gcal_ingest_periodic: user=%s failed: %s", user_id, exc)


def register_sync_jobs(application: Application) -> None:
    jq = application.job_queue
    if jq is None:
        logger.warning("register_sync_jobs: no JobQueue available")
        return

    # 06:15 — pull Granola meetings from yesterday + this morning before
    # the 07:30 briefing in Phase 4.
    jq.run_daily(
        callback=_granola_daily_pull,
        time=time(hour=6, minute=15, tzinfo=_BERLIN),
        name="granola_daily_pull",
    )
    logger.info("Granola Daily Pull registriert: 06:15 Europe/Berlin")

    # GCal ingest every 6 hours starting at 06:00. Pi-friendly cadence.
    jq.run_repeating(
        callback=_gcal_ingest_periodic,
        interval=6 * 3600,
        first=60,  # first run 60s after startup
        name="gcal_ingest_periodic",
    )
    logger.info("GCal Ingest registriert: alle 6h, 1. Lauf in 60s")
