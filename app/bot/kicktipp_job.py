"""
kicktipp_job.py — scheduled AI player + manual /kicktipp command.

Fully automatic mode: a repeating job logs in, reads the open matches of the
configured community, predicts scorelines for those kicking off within
KICKTIPP_LOOKAHEAD_HOURS, submits them (skipping already-placed unless
KICKTIPP_OVERRIDE), and notifies the admin what it tipped.

Manual:
  /kicktipp          → status (config + open matches count)
  /kicktipp dry      → predict + show tips WITHOUT submitting
  /kicktipp run      → predict + submit now
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import Application, ContextTypes

from app.bot.whitelist import require_whitelist
from app.config import (
    KICKTIPP_ENABLED, KICKTIPP_EMAIL, KICKTIPP_PASSWORD, KICKTIPP_COMMUNITY,
    KICKTIPP_LOOKAHEAD_HOURS, KICKTIPP_CHECK_INTERVAL_MINUTES, KICKTIPP_OVERRIDE,
    ADMIN_CHAT_ID,
)
from app.services.kicktipp_client import KicktippClient, KicktippError, Match
from app.services.kicktipp_predictor import predict_matchday

logger = logging.getLogger(__name__)


def _configured() -> bool:
    return bool(KICKTIPP_EMAIL and KICKTIPP_PASSWORD and KICKTIPP_COMMUNITY)


def _within_lookahead(m: Match) -> bool:
    if m.kickoff is None:
        return True  # no date parsed → don't exclude
    return m.kickoff <= datetime.now() + timedelta(hours=KICKTIPP_LOOKAHEAD_HOURS)


def _eligible(matches: list[Match], override: bool) -> list[Match]:
    out = []
    for m in matches:
        if not _within_lookahead(m):
            continue
        if m.has_bet and not override:
            continue
        out.append(m)
    return out


async def run_tips(*, dry_run: bool, override: bool | None = None) -> str:
    """Core flow shared by the scheduled job and manual triggers.
    Returns a human-readable report."""
    if not _configured():
        return "⚠️ Kicktipp nicht konfiguriert — KICKTIPP_EMAIL/PASSWORD/COMMUNITY in .env setzen."
    override = KICKTIPP_OVERRIDE if override is None else override

    try:
        async with KicktippClient(KICKTIPP_EMAIL, KICKTIPP_PASSWORD) as client:
            await client.login()
            matches = await client.get_open_matches(KICKTIPP_COMMUNITY)
            eligible = _eligible(matches, override)
            if not eligible:
                return (f"⚽ Kicktipp ({KICKTIPP_COMMUNITY}): keine offenen Spiele "
                        f"in den nächsten {KICKTIPP_LOOKAHEAD_HOURS}h zu tippen.")

            preds = await predict_matchday(eligible)
            if not preds:
                return "⚽ Keine Vorhersage erhalten (LLM-Problem) — nichts getippt."

            by_field = {m.field_home: m for m in eligible}
            lines = []
            for field_home, (h, a) in preds.items():
                m = by_field.get(field_home)
                if m:
                    lines.append(f"• {m.home} {h}:{a} {m.away}")

            if dry_run:
                header = f"🧪 *Kicktipp Dry-Run ({KICKTIPP_COMMUNITY})* — {len(preds)} Tipps (nicht abgesendet):"
                return header + "\n" + "\n".join(lines)

            written = await client.submit_tips(KICKTIPP_COMMUNITY, preds)
            header = f"✅ *Kicktipp ({KICKTIPP_COMMUNITY})* — {written} Tipps abgegeben:"
            return header + "\n" + "\n".join(lines)
    except KicktippError as exc:
        return f"❌ Kicktipp: {exc}"
    except Exception as exc:
        logger.error("kicktipp run_tips: %s", exc, exc_info=True)
        return f"❌ Kicktipp-Fehler: {exc}"


async def _scheduled_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    report = await run_tips(dry_run=False)
    logger.info("kicktipp scheduled: %s", report.splitlines()[0] if report else "—")
    # Only notify on actual submissions or errors, not on "nothing to tip"
    if ADMIN_CHAT_ID and (report.startswith("✅") or report.startswith("❌")):
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=report, parse_mode="Markdown")
        except Exception as exc:
            logger.debug("kicktipp: admin notify failed: %s", exc)


def register_kicktipp_job(application: Application) -> None:
    if not KICKTIPP_ENABLED:
        logger.info("Kicktipp deaktiviert (KICKTIPP_ENABLED=false)")
        return
    if not _configured():
        logger.warning("Kicktipp aktiviert, aber EMAIL/PASSWORD/COMMUNITY fehlen — Job nicht registriert")
        return
    jq = application.job_queue
    if jq is None:
        logger.warning("register_kicktipp_job: no JobQueue available")
        return
    interval = max(30, KICKTIPP_CHECK_INTERVAL_MINUTES) * 60
    jq.run_repeating(_scheduled_callback, interval=interval, first=120, name="kicktipp_autotip")
    logger.info(
        "Kicktipp AI-Spieler registriert: alle %d Min (Community: %s, Lookahead: %dh)",
        KICKTIPP_CHECK_INTERVAL_MINUTES, KICKTIPP_COMMUNITY, KICKTIPP_LOOKAHEAD_HOURS,
    )


# ── Manual /kicktipp handler ─────────────────────────────────────────────────


@require_whitelist
async def kicktipp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = [a.lower() for a in (context.args or [])]
    sub = args[0] if args else "status"

    if sub in ("dry", "dryrun", "test"):
        await update.message.reply_text("🧪 Kicktipp Dry-Run …")
        report = await run_tips(dry_run=True)
        await update.message.reply_text(report, parse_mode="Markdown")
        return

    if sub in ("run", "tip", "tippen"):
        await update.message.reply_text("⚽ Tippe Spiele …")
        report = await run_tips(dry_run=False)
        await update.message.reply_text(report, parse_mode="Markdown")
        return

    # default: status
    if not _configured():
        await update.message.reply_text(
            "⚽ *Kicktipp* — nicht konfiguriert.\n"
            "In `.env` setzen: `KICKTIPP_EMAIL`, `KICKTIPP_PASSWORD`, "
            "`KICKTIPP_COMMUNITY` (Gruppen-Slug aus der URL), `KICKTIPP_ENABLED=true`.",
            parse_mode="Markdown",
        )
        return
    try:
        async with KicktippClient(KICKTIPP_EMAIL, KICKTIPP_PASSWORD) as client:
            await client.login()
            matches = await client.get_open_matches(KICKTIPP_COMMUNITY)
            eligible = _eligible(matches, KICKTIPP_OVERRIDE)
        await update.message.reply_text(
            f"⚽ *Kicktipp ({KICKTIPP_COMMUNITY})*\n"
            f"Offene Spiele gesamt: {len(matches)}\n"
            f"Tippbar (≤{KICKTIPP_LOOKAHEAD_HOURS}h, offen): {len(eligible)}\n"
            f"Auto-Tipp: alle {KICKTIPP_CHECK_INTERVAL_MINUTES} Min\n\n"
            f"`/kicktipp dry` — Vorschau · `/kicktipp run` — jetzt tippen",
            parse_mode="Markdown",
        )
    except KicktippError as exc:
        await update.message.reply_text(f"❌ Kicktipp: {exc}")
    except Exception as exc:
        logger.error("kicktipp_handler status: %s", exc, exc_info=True)
        await update.message.reply_text(f"❌ Kicktipp-Fehler: {exc}")
