import logging
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from app import config

logger = logging.getLogger(__name__)


def is_allowed(user_id: int) -> bool:
    """Prüft ob ein User in der Whitelist ist."""
    allowed = config.ALLOWED_USER_IDS
    if not allowed:
        logger.warning("Whitelist ist leer — alle User blockiert!")
        return False
    return user_id in allowed


def require_whitelist(handler):
    """Decorator: lehnt Handler ab wenn user nicht in der Whitelist.
    Antwortet mit '⛔ Kein Zugriff.' falls eine Message vorhanden ist.
    """
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      *args, **kwargs):
        user = update.effective_user
        if user is None or not is_allowed(user.id):
            if update.message is not None:
                await update.message.reply_text("⛔ Kein Zugriff.")
            return None
        return await handler(update, context, *args, **kwargs)
    return wrapper
