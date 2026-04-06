import time
from collections import defaultdict, deque
from app import config

# Sliding window per user_id
_windows: dict[int, deque] = defaultdict(deque)

def is_rate_limited(user_id: int) -> tuple[bool, int]:
    """
    Sliding window rate limiter.
    Returns (limited: bool, retry_after_seconds: int).
    """
    now = time.time()
    window = _windows[user_id]

    # Alte Einträge außerhalb des Fensters entfernen
    while window and window[0] < now - config.RATE_LIMIT_WINDOW_SECONDS:
        window.popleft()

    if len(window) >= config.RATE_LIMIT_MAX_REQUESTS:
        retry_after = int(config.RATE_LIMIT_WINDOW_SECONDS - (now - window[0])) + 1
        return True, retry_after

    window.append(now)
    return False, 0
