import logging
from collections import deque
from functools import wraps
from typing import TYPE_CHECKING

from app import config
from app.security.rate_limiter import is_rate_limited

if TYPE_CHECKING:  # nur für Type-Hints — telegram ist in CI-Deps nicht enthalten
    from telegram import Update
    from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Telegram-Retry-Schutz: bereits gesehene update_ids.
_seen_update_ids: deque[int] = deque(maxlen=1000)


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
    async def wrapper(update: "Update", context: "ContextTypes.DEFAULT_TYPE",
                      *args, **kwargs):
        user = update.effective_user
        if user is None or not is_allowed(user.id):
            if update.message is not None:
                await update.message.reply_text("⛔ Kein Zugriff.")
            return None
        return await handler(update, context, *args, **kwargs)
    return wrapper


def guarded(handler):
    """Decorator-Stack für Message-Handler: Dedup + Whitelist + Rate-Limit.
    Lehnt ab wenn duplizierter update_id, nicht-whitelisted oder rate-limited.
    """
    @wraps(handler)
    async def wrapper(update: "Update", context: "ContextTypes.DEFAULT_TYPE",
                      *args, **kwargs):
        uid = update.update_id
        if uid in _seen_update_ids:
            logger.debug("Doppelte update_id %d ignoriert", uid)
            return None
        _seen_update_ids.append(uid)

        user = update.effective_user
        if user is None or not is_allowed(user.id):
            if update.message is not None:
                await update.message.reply_text("⛔ Kein Zugriff.")
            return None

        limited, retry_after = is_rate_limited(user.id)
        if limited:
            logger.warning("Rate limit exceeded | user=%d", user.id)
            if update.message is not None:
                await update.message.reply_text(
                    f"⏳ Zu viele Nachrichten. Bitte {retry_after}s warten."
                )
            return None

        return await handler(update, context, *args, **kwargs)
    return wrapper
