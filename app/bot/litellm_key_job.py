"""
litellm_key_job.py — reminder that the LiteLLM API key must be renewed.

The LiteLLM key expires every LITELLM_KEY_RENEW_DAYS days. This job tracks when
the current key was first seen and pings the admin on Telegram one day before
expiry (and again once expired). When the key value changes (i.e. the user
renewed it in .env and restarted), the timer auto-resets — no manual step.

State: app/data/litellm_key_state.json  {"fingerprint": "...", "set_date": "YYYY-MM-DD"}
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram.ext import Application, ContextTypes

from app.config import (
    LITELLM_API_KEY, LITELLM_KEY_RENEW_DAYS,
    LITELLM_KEY_REMINDER_HOUR, LITELLM_KEY_REMINDER_MINUTE,
    ADMIN_CHAT_ID,
)

logger = logging.getLogger(__name__)

_BERLIN = ZoneInfo("Europe/Berlin")
_STATE_FILE = Path(__file__).parent.parent / "data" / "litellm_key_state.json"


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] if key else ""


def _load() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("litellm_key: state save failed: %s", exc)


def _sync_key_date(today: date | None = None) -> dict:
    """Record today as the set-date when the key is new/changed. Returns state."""
    today = today or datetime.now(tz=_BERLIN).date()
    state = _load()
    fp = _fingerprint(LITELLM_API_KEY)
    if not fp:
        return state
    if state.get("fingerprint") != fp:
        state = {"fingerprint": fp, "set_date": today.isoformat()}
        _save(state)
        logger.info("litellm_key: neuer Key erkannt → Renewal-Timer auf %s gesetzt", today.isoformat())
    return state


def _key_age_days(state: dict, today: date) -> int | None:
    try:
        set_date = datetime.strptime(state["set_date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return None
    return (today - set_date).days


async def _reminder_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not LITELLM_API_KEY or not ADMIN_CHAT_ID:
        return
    today = datetime.now(tz=_BERLIN).date()
    state = _sync_key_date(today)
    age = _key_age_days(state, today)
    if age is None:
        return

    renew = LITELLM_KEY_RENEW_DAYS
    msg = None
    if age >= renew:
        msg = (f"🔑 *LiteLLM-Key abgelaufen* (gesetzt vor {age} Tagen, Limit {renew}d).\n"
               f"Bitte erneuern und in `.env` `LITELLM_API_KEY` setzen + Bot neu starten.")
    elif age >= renew - 1:
        msg = (f"🔑 *LiteLLM-Key läuft morgen ab* (gesetzt vor {age} Tagen, Limit {renew}d).\n"
               f"Bitte demnächst erneuern und in `.env` setzen.")
    if not msg:
        return
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode="Markdown")
        logger.info("litellm_key: Renewal-Reminder gesendet (age=%dd)", age)
    except Exception as exc:
        logger.warning("litellm_key: reminder send failed: %s", exc)


def register_litellm_key_job(application: Application) -> None:
    if not LITELLM_API_KEY:
        logger.info("LiteLLM-Key-Reminder: kein LITELLM_API_KEY — nicht registriert")
        return
    _sync_key_date()  # capture/auto-reset the set-date on every startup
    jq = application.job_queue
    if jq is None:
        logger.warning("register_litellm_key_job: no JobQueue available")
        return
    jq.run_daily(
        _reminder_callback,
        time=time(hour=LITELLM_KEY_REMINDER_HOUR, minute=LITELLM_KEY_REMINDER_MINUTE, tzinfo=_BERLIN),
        name="litellm_key_reminder",
    )
    logger.info(
        "LiteLLM-Key-Reminder registriert: täglich %02d:%02d, Renewal alle %d Tage",
        LITELLM_KEY_REMINDER_HOUR, LITELLM_KEY_REMINDER_MINUTE, LITELLM_KEY_RENEW_DAYS,
    )
