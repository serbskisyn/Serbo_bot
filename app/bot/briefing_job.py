"""
briefing_job.py — Schedule + manual trigger for the consolidated morning digest.

Cron-style: every day at BRIEFING_HOUR:BRIEFING_MINUTE Europe/Berlin,
push the digest to every user in NEWS_DAILY_PUSH_USER_IDS. The digest
combines briefing (calendar + todos + decisions + relationship alerts +
backtest-pulse) with trade-engine status (crypto + stocks) and the
7-day R/Kelly recap — replacing the earlier 06:30 calendar / 07:30
briefing / 08:15 trading-stats trio.

A small idempotency-marker (briefing_state.json) records the last send
date per user so a service restart in the morning doesn't re-trigger
the push. Manual /briefing always sends regardless.
"""
from __future__ import annotations

import json
import logging
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, ContextTypes

from app.bot.whitelist import require_whitelist
from app.config import (
    BRIEFING_ENABLED, BRIEFING_HOUR, BRIEFING_MINUTE,
    NEWS_DAILY_PUSH_USER_IDS,
)
from app.services.briefing import assemble_daily_digest

logger = logging.getLogger(__name__)

_BERLIN = ZoneInfo("Europe/Berlin")
_STATE_FILE = Path(__file__).parent.parent / "data" / "briefing_state.json"


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("briefing_state: save failed: %s", exc)


async def _send_briefing(bot, user_id: int) -> bool:
    try:
        text = await assemble_daily_digest(user_id)
        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:
        logger.warning("briefing: send to user=%s failed: %s", user_id, exc)
        return False


async def _daily_briefing_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not NEWS_DAILY_PUSH_USER_IDS:
        return
    today = date.today().isoformat()
    state = _load_state()
    for user_id in NEWS_DAILY_PUSH_USER_IDS:
        key = str(user_id)
        if state.get(key) == today:
            logger.info("briefing: user=%s already sent today, skipping", user_id)
            continue
        ok = await _send_briefing(context.bot, user_id)
        if ok:
            state[key] = today
            logger.info("briefing: sent to user=%s", user_id)
    _save_state(state)


def register_briefing_job(application: Application) -> None:
    if not BRIEFING_ENABLED:
        logger.info("Daily Briefing deaktiviert (BRIEFING_ENABLED=false)")
        return
    if not NEWS_DAILY_PUSH_USER_IDS:
        logger.info("Daily Briefing: keine User in NEWS_DAILY_PUSH_USER_IDS — nicht registriert")
        return
    jq = application.job_queue
    if jq is None:
        logger.warning("register_briefing_job: no JobQueue available")
        return
    jq.run_daily(
        callback=_daily_briefing_callback,
        time=time(hour=BRIEFING_HOUR, minute=BRIEFING_MINUTE, tzinfo=_BERLIN),
        name="daily_briefing",
    )
    logger.info(
        "Daily Briefing registriert: %02d:%02d Europe/Berlin (%d Empfänger)",
        BRIEFING_HOUR, BRIEFING_MINUTE, len(NEWS_DAILY_PUSH_USER_IDS),
    )


# ── Manual /briefing handler ─────────────────────────────────────────────────


@require_whitelist
async def briefing_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text("⏳ Briefing wird zusammengestellt …")
    try:
        text = await assemble_daily_digest(user_id)
        await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as exc:
        logger.error("briefing_handler: %s", exc, exc_info=True)
        await update.message.reply_text("❌ Briefing konnte nicht erstellt werden.")
