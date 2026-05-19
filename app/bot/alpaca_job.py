import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.trading_job import tradebot_handler

logger = logging.getLogger(__name__)


async def stocks_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias für /tradebot — zeigt kombinierten Status."""
    await tradebot_handler(update, context)


def register_alpaca_jobs(application) -> None:
    # Scans laufen jetzt in der Trade Engine — kein eigener Job-Schedule mehr nötig
    logger.info("Alpaca Scans: delegiert an Trade Engine (%s)", TRADE_ENGINE_URL)
