import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.whitelist import is_allowed
from app.config import ADMIN_CHAT_ID, ALPACA_SCAN_HOUR, ALPACA_SCAN_MINUTE, ALPACA_API_KEY
from app.services.alpaca_client import fetch_alpaca_status, run_alpaca_scan

logger = logging.getLogger(__name__)

_HELP = (
    "📈 *Alpaca Aktien-Bot*\n\n"
    "`/stocks` — Status, Positionen & P&L\n"
    "`/stocks scan` — Manuellen Scan starten\n"
    "`/stocks help` — Diese Übersicht"
)


async def stocks_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    if not ALPACA_API_KEY:
        await update.message.reply_text(
            "⚠️ Alpaca nicht konfiguriert.\n"
            "Bitte `ALPACA_API_KEY` und `ALPACA_SECRET_KEY` in `.env` eintragen."
        )
        return

    sub = (context.args[0].lower() if context.args else "").strip()
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    if sub == "help":
        await update.message.reply_text(_HELP, parse_mode="Markdown")
        return

    if sub == "scan":
        chat_id = update.effective_chat.id

        async def notify(text: str):
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")

        result = await run_alpaca_scan(notify_fn=notify)
        await update.message.reply_text(result, parse_mode="Markdown")
        return

    # Default: Status
    report = await fetch_alpaca_status()
    await update.message.reply_text(report, parse_mode="Markdown")


async def _run_scheduled_scan(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_CHAT_ID or not ALPACA_API_KEY:
        return

    async def notify(text: str):
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, text=text, parse_mode="Markdown"
        )

    try:
        result = await run_alpaca_scan(notify_fn=notify)
        # Nur melden wenn tatsächlich etwas passiert ist
        if "kein Signal" not in result and result:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID, text=result, parse_mode="Markdown"
            )
        logger.info("Alpaca Scheduled Scan abgeschlossen: %s", result[:80])
    except Exception as e:
        logger.error("Alpaca Scan fehlgeschlagen: %s", e)


def register_alpaca_jobs(application) -> None:
    if not ALPACA_API_KEY:
        logger.info("Alpaca nicht konfiguriert — Jobs übersprungen.")
        return

    jq = application.job_queue
    if jq is None:
        logger.warning("JobQueue nicht verfügbar — Alpaca Jobs deaktiviert.")
        return

    ET = ZoneInfo("America/New_York")

    # Stündliche Scans während Marktzeiten (10:00–15:45 ET = 16:00–21:45 CEST)
    for hour in range(10, 16):
        for minute in [0, 15, 30, 45]:
            if hour == 15 and minute > 45:
                continue
            jq.run_daily(
                callback=_run_scheduled_scan,
                time=time(hour=hour, minute=minute, tzinfo=ET),
                days=(0, 1, 2, 3, 4),  # Mo–Fr
                name=f"alpaca_scan_{hour:02d}{minute:02d}",
            )

    logger.info(
        "Alpaca Jobs registriert: Mo–Fr 10:00–15:45 ET (alle 15 Min), "
        "täglicher Status %02d:%02d ET",
        ALPACA_SCAN_HOUR, ALPACA_SCAN_MINUTE,
    )
