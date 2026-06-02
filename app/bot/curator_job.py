"""
curator_job.py — schedule + manual trigger for the profile curator.

Scheduled daily at CURATOR_HOUR:CURATOR_MINUTE, but the curator's own
cooldown (CURATOR_COOLDOWN_DAYS) gates whether it actually runs — so in
practice it produces a proposal at most weekly. When it finds something to
consolidate it pushes the dry-run report to the user, who confirms with
/curator apply.

Manual:
  /curator           → status
  /curator run       → force a dry-run now (ignores cooldown)
  /curator apply     → apply the pending proposal
  /curator cancel    → discard the pending proposal
"""
from __future__ import annotations

import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, ContextTypes

from app.bot.whitelist import require_whitelist
from app.config import (
    CURATOR_ENABLED, CURATOR_HOUR, CURATOR_MINUTE,
    NEWS_DAILY_PUSH_USER_IDS,
)
from app.services import curator

logger = logging.getLogger(__name__)

_BERLIN = ZoneInfo("Europe/Berlin")


async def _daily_curator_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    for user_id in NEWS_DAILY_PUSH_USER_IDS:
        try:
            report = await curator.run_dry_run(user_id, force=False)
        except Exception as exc:
            logger.warning("curator: dry-run for user=%s failed: %s", user_id, exc)
            continue
        if not report:
            continue
        try:
            await context.bot.send_message(
                chat_id=user_id, text=report, parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            logger.info("curator: proposal pushed to user=%s", user_id)
        except Exception as exc:
            logger.warning("curator: push to user=%s failed: %s", user_id, exc)


def register_curator_job(application: Application) -> None:
    if not CURATOR_ENABLED:
        logger.info("Curator deaktiviert (CURATOR_ENABLED=false)")
        return
    if not NEWS_DAILY_PUSH_USER_IDS:
        logger.info("Curator: keine User in NEWS_DAILY_PUSH_USER_IDS — nicht registriert")
        return
    jq = application.job_queue
    if jq is None:
        logger.warning("register_curator_job: no JobQueue available")
        return
    jq.run_daily(
        callback=_daily_curator_callback,
        time=time(hour=CURATOR_HOUR, minute=CURATOR_MINUTE, tzinfo=_BERLIN),
        name="daily_curator",
    )
    logger.info(
        "Curator registriert: %02d:%02d Europe/Berlin (Cooldown gated)",
        CURATOR_HOUR, CURATOR_MINUTE,
    )


# ── Manual /curator handler ──────────────────────────────────────────────────


@require_whitelist
async def curator_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = [a.lower() for a in (context.args or [])]
    sub = args[0] if args else "status"

    if sub == "apply":
        ok, msg = await curator.apply_pending(user_id)
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    if sub == "cancel":
        await update.message.reply_text(curator.cancel_pending(user_id), parse_mode="Markdown")
        return

    if sub == "run":
        await update.message.reply_text("🧹 Analysiere Profil …")
        try:
            report = await curator.run_dry_run(user_id, force=True)
        except Exception as exc:
            logger.error("curator_handler run: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Analyse fehlgeschlagen.")
            return
        if not report:
            await update.message.reply_text("✅ Nichts zu bereinigen — Profil ist sauber.")
        else:
            await update.message.reply_text(report, parse_mode="Markdown",
                                            disable_web_page_preview=True)
        return

    # default: status
    await update.message.reply_text(curator.get_status(user_id), parse_mode="Markdown")
