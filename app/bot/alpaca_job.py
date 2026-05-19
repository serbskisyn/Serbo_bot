import logging

from app.config import TRADE_ENGINE_URL

logger = logging.getLogger(__name__)


def register_alpaca_jobs(application) -> None:
    logger.info("Alpaca Scans: delegiert an Trade Engine (%s)", TRADE_ENGINE_URL)
