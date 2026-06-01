import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.whitelist import is_allowed
from app.config import ADMIN_CHAT_ID, TRADING_STATS_HOUR, TRADING_STATS_MINUTE
from app.services.trade_engine_client import fetch_status, fetch_crypto_status, trigger_scan, control_crypto
from app.services.trade_recap import build_recap
from app.services.papertrade_status import build_papertrade_status

logger = logging.getLogger(__name__)

_HELP_CRYPTO = (
    "🪙 *Crypto-Befehle*\n\n"
    "`/tradebot crypto pause` — Neue Käufe stoppen\n"
    "`/tradebot crypto resume` — Käufe wieder aktivieren\n"
    "`/tradebot crypto stop` — Alias für pause\n"
    "`/tradebot crypto start` — Alias für resume\n"
    "`/tradebot crypto help` — Diese Übersicht"
)

_HELP_STOCKS = (
    "📈 *Stocks-Befehle*\n\n"
    "`/tradebot stocks scan` — Manuellen LLM-Scan starten\n"
    "`/tradebot stocks help` — Diese Übersicht"
)

_HELP_FULL = (
    "🤖 *Trading Bot*\n\n"
    "`/tradebot` — Kombinierter Status (Crypto + Stocks)\n\n"
    + _HELP_CRYPTO + "\n\n"
    + _HELP_STOCKS
)


async def tradebot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    args = [a.lower() for a in (context.args or [])]
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # /tradebot
    if not args:
        reply = await fetch_status()
        await update.message.reply_text(reply, parse_mode="Markdown")
        return

    # /tradebot help
    if args[0] == "help":
        await update.message.reply_text(_HELP_FULL, parse_mode="Markdown")
        return

    # /tradebot crypto <action>
    if args[0] == "crypto":
        action = args[1] if len(args) > 1 else ""
        if action == "help" or not action:
            await update.message.reply_text(_HELP_CRYPTO, parse_mode="Markdown")
        elif action in ("pause", "resume", "stop", "start"):
            reply = await control_crypto(action)
            await update.message.reply_text(reply, parse_mode="Markdown")
        else:
            await update.message.reply_text(
                f"⚠️ Unbekannte Aktion: `{action}`\n\n" + _HELP_CRYPTO, parse_mode="Markdown"
            )
        return

    # /tradebot stocks <action>
    if args[0] == "stocks":
        action = args[1] if len(args) > 1 else ""
        if action == "help" or not action:
            await update.message.reply_text(_HELP_STOCKS, parse_mode="Markdown")
        elif action == "scan":
            reply = await trigger_scan("stocks")
            await update.message.reply_text(reply, parse_mode="Markdown")
        else:
            await update.message.reply_text(
                f"⚠️ Unbekannte Aktion: `{action}`\n\n" + _HELP_STOCKS, parse_mode="Markdown"
            )
        return

    await update.message.reply_text(
        f"⚠️ Unbekannte Option: `{args[0]}`\n\n" + _HELP_FULL, parse_mode="Markdown"
    )


async def send_daily_trading_stats(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_CHAT_ID:
        return
    try:
        report = await fetch_crypto_status()
        # Append the 7-day R/Kelly + live-trade pulse so the user can track
        # the edge evolution daily.
        try:
            recap = build_recap(days=7)
            report = f"{report}\n\n{recap}"
        except Exception as exc:
            logger.warning("daily_trading_stats: recap section skipped: %s", exc)
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, text=report, parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("Trading Stats senden fehlgeschlagen: %s", e)


async def recap_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual /recap — show the 7-day R/Kelly + live-trade pulse on demand."""
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    # Optional arg: /recap 14 → last 14 days
    days = 7
    if context.args:
        try:
            days = max(1, min(30, int(context.args[0])))
        except ValueError:
            pass
    try:
        text = build_recap(days=days)
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as exc:
        logger.error("recap_handler: %s", exc, exc_info=True)
        await update.message.reply_text("❌ Recap konnte nicht erstellt werden.")


async def papertrade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual /papertrade — show dry-run phantom positions + simulation stats."""
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        text = await build_papertrade_status()
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as exc:
        logger.error("papertrade_handler: %s", exc, exc_info=True)
        await update.message.reply_text("❌ Paper-Trading-Status konnte nicht geladen werden.")


def register_trading_stats_job(application) -> None:
    from datetime import time
    from zoneinfo import ZoneInfo

    jq = application.job_queue
    if jq is None:
        return

    jq.run_daily(
        callback=send_daily_trading_stats,
        time=time(hour=TRADING_STATS_HOUR, minute=TRADING_STATS_MINUTE,
                  tzinfo=ZoneInfo("Europe/Berlin")),
        name="daily_trading_stats",
    )
    logger.info("Daily Trading Stats registriert: %02d:%02d Europe/Berlin",
                TRADING_STATS_HOUR, TRADING_STATS_MINUTE)
