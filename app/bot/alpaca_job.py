import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.whitelist import is_allowed
from app.config import TRADE_ENGINE_URL
from app.services.trade_engine_client import fetch_stocks_status, trigger_scan

logger = logging.getLogger(__name__)

_HELP = (
    "📈 *Alpaca Aktien-Bot*\n\n"
    "`/stocks` — Status, Positionen & P&L\n"
    "`/stocks scan` — Manuellen Scan auslösen\n"
    "`/stocks help` — Diese Übersicht"
)


async def stocks_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    sub = (context.args[0].lower() if context.args else "").strip()
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    if sub == "help":
        await update.message.reply_text(_HELP, parse_mode="Markdown")
    elif sub == "scan":
        reply = await trigger_scan("stocks")
        await update.message.reply_text(reply, parse_mode="Markdown")
    else:
        reply = await fetch_stocks_status()
        await update.message.reply_text(reply, parse_mode="Markdown")


def register_alpaca_jobs(application) -> None:
    # Scans laufen jetzt in der Trade Engine — kein eigener Job-Schedule mehr nötig
    logger.info("Alpaca Scans: delegiert an Trade Engine (%s)", TRADE_ENGINE_URL)
