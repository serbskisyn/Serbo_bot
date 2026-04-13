import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL   = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
BOT_NAME           = os.getenv("BOT_NAME", "MeinAgent")
LOG_LEVEL          = os.getenv("LOG_LEVEL", "INFO")
TAVILY_API_KEY     = os.getenv("TAVILY_API_KEY")
GNEWS_API_KEY      = os.getenv("GNEWS_API_KEY", "")

ALLOWED_USER_IDS: set[int] = set(
    int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()
)

# Rate Limiting
RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", 10))
RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", 60))

# ── News Cache ────────────────────────────────────────────────────────────────

# SQLite-Datei fuer den News-Cache
NEWS_CACHE_DB_PATH: str = os.getenv("NEWS_CACHE_DB_PATH", "app/data/news_cache.db")

# Clubs die IMMER im Hintergrund gecacht werden (kommagetrennt in .env)
_raw_favorites = os.getenv("NEWS_FAVORITE_CLUBS", "Borussia Dortmund,Dynamo Dresden")
NEWS_FAVORITE_CLUBS: list[str] = [
    c.strip() for c in _raw_favorites.split(",") if c.strip()
]

# Scheduler: Basis-Intervall zwischen Refreshes in Minuten
NEWS_SCHEDULER_BASE_MINUTES: int = int(os.getenv("NEWS_SCHEDULER_BASE_MINUTES", 45))

# Scheduler: Jitter ± Minuten (zufaellige Abweichung vom Basis-Intervall)
NEWS_SCHEDULER_JITTER_MINUTES: int = int(os.getenv("NEWS_SCHEDULER_JITTER_MINUTES", 15))

# Cache gilt als abgelaufen nach X Stunden (kein Stale-Label, echter Fallback auf Live-Fetch)
NEWS_CACHE_MAX_AGE_HOURS: int = int(os.getenv("NEWS_CACHE_MAX_AGE_HOURS", 48))

# Ab X Stunden wird das "veraltet"-Label angezeigt (trotzdem aus Cache geliefert)
NEWS_STALE_LABEL_HOURS: int = int(os.getenv("NEWS_STALE_LABEL_HOURS", 4))


def validate_config():
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not OPENROUTER_API_KEY:
        missing.append("OPENROUTER_API_KEY")
    if missing:
        raise ValueError(f"Fehlende Umgebungsvariablen: {', '.join(missing)}")
