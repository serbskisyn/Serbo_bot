import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from app.config import LOG_LEVEL

LOG_DIR = Path(__file__).parent.parent.parent / "logs"


def setup_logging():
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    LOG_DIR.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_DIR / "serbo_bot.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(fmt))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(fmt))

    logging.basicConfig(level=level, handlers=[console_handler, file_handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
