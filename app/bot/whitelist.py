import logging
from app import config

logger = logging.getLogger(__name__)

def is_allowed(user_id: int) -> bool:
    """Prüft ob ein User in der Whitelist ist."""
    allowed = config.ALLOWED_USER_IDS
    if not allowed:
        logger.warning("Whitelist ist leer — alle User blockiert!")
        return False
    return user_id in allowed
