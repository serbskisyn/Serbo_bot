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
    # Community is optional — if unset we tip every round the user is in.
    return bool(KICKTIPP_EMAIL and KICKTIPP_PASSWORD)


_MAX_MATCHDAYS = 25   # safety cap when scanning matchdays forward


def _eligible(matches: list[Match], override: bool) -> list[Match]:
    """Tippable now: known teams, kickoff in [now, now+lookahead], and
    (untipped or override)."""
    now = datetime.now()
    horizon = now + timedelta(hours=KICKTIPP_LOOKAHEAD_HOURS)
    out = []
    for m in matches:
        if "unbekannt" in f"{m.home}{m.away}".lower():
            continue  # knockout pairing not decided yet
        if m.kickoff is not None and not (now <= m.kickoff <= horizon):
            continue
        if m.has_bet and not override:
            continue
        out.append(m)
    return out


async def _tip_community(client: KicktippClient, community: str, override: bool,
                         dry_run: bool) -> tuple[list[str], int]:
    """Tip eligible matches across ALL upcoming matchdays of one community
    (not just the default view). Returns (report_lines, written_count)."""
    now = datetime.now()
    horizon = now + timedelta(hours=KICKTIPP_LOOKAHEAD_HOURS)
    lines: list[str] = []
    written = 0
    for idx in range(1, _MAX_MATCHDAYS + 1):
        try:
            matches = await client.get_open_matches(community, matchday=idx)
        except Exception as exc:
            logger.debug("kicktipp: %s ST%d fetch failed: %s", community, idx, exc)
            continue
        if not matches:
            continue
        known = [m for m in matches if "unbekannt" not in f"{m.home}{m.away}".lower()]
        # Matchdays are chronological — once a whole matchday starts beyond the
        # lookahead horizon, every later one does too, so stop scanning.
        kickoffs = [m.kickoff for m in known if m.kickoff]
        if kickoffs and min(kickoffs) > horizon:
            break
        eligible = _eligible(matches, override)
        if not eligible:
            continue
        preds = await predict_matchday(eligible)
        if not preds:
            continue
        if not dry_run:
            written += await client.submit_tips(community, preds, matchday=idx)
        by = {m.field_home: m for m in eligible}
        for fh, (h, a) in preds.items():
            m = by.get(fh)
            if m:
                lines.append(f"• [{community} ST{idx}] {m.home} {h}:{a} {m.away}")
    return lines, written


async def run_tips(*, dry_run: bool, override: bool | None = None) -> str:
    """Tip every eligible match across all matchdays in lookahead, for every
    round the user is in (or just KICKTIPP_COMMUNITY if that's set)."""
    if not _configured():
        return "⚠️ Kicktipp nicht konfiguriert — KICKTIPP_EMAIL/PASSWORD in .env setzen."
    override = KICKTIPP_OVERRIDE if override is None else override
    try:
        async with KicktippClient(KICKTIPP_EMAIL, KICKTIPP_PASSWORD) as client:
            await client.login()
            communities = [KICKTIPP_COMMUNITY] if KICKTIPP_COMMUNITY else await client.get_communities()
            if not communities:
                return "⚽ Keine Tipprunde gefunden."
            all_lines: list[str] = []
            grand_written = 0
            for community in communities:
                lines, written = await _tip_community(client, community, override, dry_run)
                all_lines += lines
                grand_written += written
            if not all_lines:
                return f"⚽ Kicktipp: keine offenen Spiele in den nächsten {KICKTIPP_LOOKAHEAD_HOURS}h."
            if dry_run:
                return f"🧪 *Kicktipp Dry-Run* — {len(all_lines)} Tipps (nicht abgesendet):\n" + "\n".join(all_lines)
            return f"✅ *Kicktipp* — {grand_written} Tipps abgegeben:\n" + "\n".join(all_lines)
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

    # default: status — light scan (no predictions), counts eligible per round
    if not _configured():
        await update.message.reply_text(
            "⚽ *Kicktipp* — nicht konfiguriert.\n"
            "In `.env` setzen: `KICKTIPP_EMAIL`, `KICKTIPP_PASSWORD`, "
            "`KICKTIPP_ENABLED=true` (optional `KICKTIPP_COMMUNITY`).",
            parse_mode="Markdown",
        )
        return
    try:
        async with KicktippClient(KICKTIPP_EMAIL, KICKTIPP_PASSWORD) as client:
            await client.login()
            communities = [KICKTIPP_COMMUNITY] if KICKTIPP_COMMUNITY else await client.get_communities()
            now = datetime.now()
            horizon = now + timedelta(hours=KICKTIPP_LOOKAHEAD_HOURS)
            per_round = []
            for community in communities:
                eligible_total = 0
                for idx in range(1, _MAX_MATCHDAYS + 1):
                    try:
                        ms = await client.get_open_matches(community, matchday=idx)
                    except Exception:
                        continue
                    if not ms:
                        continue
                    known = [m for m in ms if "unbekannt" not in f"{m.home}{m.away}".lower()]
                    kos = [m.kickoff for m in known if m.kickoff]
                    if kos and min(kos) > horizon:
                        break
                    eligible_total += len(_eligible(ms, KICKTIPP_OVERRIDE))
                per_round.append(f"• {community}: {eligible_total} tippbar (≤{KICKTIPP_LOOKAHEAD_HOURS}h)")
        rounds_txt = "\n".join(per_round) or "keine Runde gefunden"
        await update.message.reply_text(
            f"⚽ *Kicktipp — Opus Maximus*\n"
            f"{rounds_txt}\n"
            f"Override: {'an' if KICKTIPP_OVERRIDE else 'aus'} · "
            f"Auto-Tipp alle {KICKTIPP_CHECK_INTERVAL_MINUTES} Min\n\n"
            f"`/kicktipp dry` — Vorschau · `/kicktipp run` — jetzt tippen",
            parse_mode="Markdown",
        )
    except KicktippError as exc:
        await update.message.reply_text(f"❌ Kicktipp: {exc}")
    except Exception as exc:
        logger.error("kicktipp_handler status: %s", exc, exc_info=True)
        await update.message.reply_text(f"❌ Kicktipp-Fehler: {exc}")
