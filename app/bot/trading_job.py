import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.whitelist import is_allowed
from app.config import ADMIN_CHAT_ID, TRADING_STATS_HOUR, TRADING_STATS_MINUTE
from app.services.trading_status import fetch_trading_status, send_bot_command

logger = logging.getLogger(__name__)


_HELP = (
    "📟 *Trading Bot Steuerung*\n\n"
    "`/tradebot` — Status & P&L\n"
    "`/tradebot pause` — Neue Käufe stoppen (offene Trades laufen)\n"
    "`/tradebot resume` — Käufe wieder aktivieren\n"
    "`/tradebot stop` — Bot komplett anhalten\n"
    "`/tradebot start` — Bot starten\n"
    "`/tradebot help` — Diese Übersicht"
)

_CONTROL_CMDS = {"pause", "resume", "stop", "start"}


async def tradebot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    sub = (context.args[0].lower() if context.args else "").strip()

    if sub == "help":
        await update.message.reply_text(_HELP, parse_mode="Markdown")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    if sub in _CONTROL_CMDS:
        reply = await send_bot_command(sub)
    else:
        reply = await fetch_trading_status()

    await update.message.reply_text(reply, parse_mode="Markdown")


async def send_daily_trading_stats(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_CHAT_ID:
        logger.warning("Trading Stats: ADMIN_CHAT_ID nicht gesetzt")
        return
    try:
        report = await fetch_trading_status()
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=report,
            parse_mode="Markdown",
        )
        logger.info("Daily Trading Stats gesendet an chat_id=%s", ADMIN_CHAT_ID)
    except Exception as e:
        logger.error("Trading Stats senden fehlgeschlagen: %s", e)


def register_trading_stats_job(application) -> None:
    from datetime import time
    from zoneinfo import ZoneInfo

    jq = application.job_queue
    if jq is None:
        logger.warning("JobQueue nicht verfügbar — Trading Stats deaktiviert")
        return

    jq.run_daily(
        callback=send_daily_trading_stats,
        time=time(hour=TRADING_STATS_HOUR, minute=TRADING_STATS_MINUTE, tzinfo=ZoneInfo("Europe/Berlin")),
        name="daily_trading_stats",
    )
    logger.info("Daily Trading Stats registriert: %02d:%02d Europe/Berlin", TRADING_STATS_HOUR, TRADING_STATS_MINUTE)
