"""
evening_job.py — Daily evening reflection scheduler + /reflect handler.
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
    NEWS_DAILY_PUSH_USER_IDS,
    REFLECTION_ENABLED, REFLECTION_HOUR, REFLECTION_MINUTE,
)
from app.services.evening_reflection import assemble_evening_reflection, write_day_summary

logger = logging.getLogger(__name__)

_BERLIN = ZoneInfo("Europe/Berlin")
_STATE_FILE = Path(__file__).parent.parent / "data" / "reflection_state.json"


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
        logger.warning("reflection_state: save failed: %s", exc)


async def _send_reflection(bot, user_id: int) -> bool:
    try:
        text = await assemble_evening_reflection(user_id)
        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        await write_day_summary(user_id, text)
        return True
    except Exception as exc:
        logger.warning("evening: send to user=%s failed: %s", user_id, exc)
        return False


async def _evening_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not NEWS_DAILY_PUSH_USER_IDS:
        return
    today = date.today().isoformat()
    state = _load_state()
    for user_id in NEWS_DAILY_PUSH_USER_IDS:
        key = str(user_id)
        if state.get(key) == today:
            logger.info("evening: user=%s already sent today, skipping", user_id)
            continue
        if await _send_reflection(context.bot, user_id):
            state[key] = today
            logger.info("evening: sent to user=%s", user_id)
    _save_state(state)


def register_evening_job(application: Application) -> None:
    if not REFLECTION_ENABLED:
        logger.info("Evening Reflection deaktiviert (REFLECTION_ENABLED=false)")
        return
    if not NEWS_DAILY_PUSH_USER_IDS:
        logger.info("Evening Reflection: keine User in NEWS_DAILY_PUSH_USER_IDS — nicht registriert")
        return
    jq = application.job_queue
    if jq is None:
        logger.warning("register_evening_job: no JobQueue available")
        return
    jq.run_daily(
        callback=_evening_callback,
        time=time(hour=REFLECTION_HOUR, minute=REFLECTION_MINUTE, tzinfo=_BERLIN),
        name="evening_reflection",
    )
    logger.info(
        "Evening Reflection registriert: %02d:%02d Europe/Berlin (%d Empfänger)",
        REFLECTION_HOUR, REFLECTION_MINUTE, len(NEWS_DAILY_PUSH_USER_IDS),
    )


# ── Manual /reflect handler ──────────────────────────────────────────────────


@require_whitelist
async def reflect_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    try:
        text = await assemble_evening_reflection(user_id)
        await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)
        await write_day_summary(user_id, text)
    except Exception as exc:
        logger.error("reflect_handler: %s", exc, exc_info=True)
        await update.message.reply_text("❌ Tagesabschluss konnte nicht erstellt werden.")
