import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.whitelist import is_allowed
from app.config import ADMIN_CHAT_ID, TRADING_STATS_HOUR, TRADING_STATS_MINUTE
from app.services.trade_engine_client import fetch_crypto_status, trigger_scan

logger = logging.getLogger(__name__)

_HELP = (
    "🪙 *Crypto Trading Bot*\n\n"
    "`/tradebot` — Status & offene Positionen\n"
    "`/tradebot scan` — Manuellen Scan auslösen\n"
    "`/tradebot help` — Diese Übersicht"
)


async def tradebot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    sub = (context.args[0].lower() if context.args else "").strip()
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    if sub == "help":
        await update.message.reply_text(_HELP, parse_mode="Markdown")
    elif sub == "scan":
        reply = await trigger_scan("crypto")
        await update.message.reply_text(reply, parse_mode="Markdown")
    else:
        reply = await fetch_crypto_status()
        await update.message.reply_text(reply, parse_mode="Markdown")


async def send_daily_trading_stats(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_CHAT_ID:
        return
    try:
        report = await fetch_crypto_status()
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, text=report, parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("Trading Stats senden fehlgeschlagen: %s", e)


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
