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
BRAVE_API_KEY      = os.getenv("BRAVE_API_KEY", "")

ALLOWED_USER_IDS: set[int] = set(
    int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()
)

# Rate Limiting
RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", 10))
RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", 60))

# ── News Cache ────────────────────────────────────────────────────────────────
NEWS_CACHE_DB_PATH: str = os.getenv("NEWS_CACHE_DB_PATH", "app/data/news_cache.db")

_raw_favorites = os.getenv("NEWS_FAVORITE_CLUBS", "Borussia Dortmund,Dynamo Dresden")
NEWS_FAVORITE_CLUBS: list[str] = [
    c.strip() for c in _raw_favorites.split(",") if c.strip()
]

NEWS_SCHEDULER_BASE_MINUTES: int = int(os.getenv("NEWS_SCHEDULER_BASE_MINUTES", 45))
NEWS_SCHEDULER_JITTER_MINUTES: int = int(os.getenv("NEWS_SCHEDULER_JITTER_MINUTES", 15))
NEWS_CACHE_MAX_AGE_HOURS: int = int(os.getenv("NEWS_CACHE_MAX_AGE_HOURS", 48))
NEWS_STALE_LABEL_HOURS: int = int(os.getenv("NEWS_STALE_LABEL_HOURS", 4))

# ── Daily News Push ───────────────────────────────────────────────────────────
NEWS_DAILY_PUSH_HOUR:   int = int(os.getenv("NEWS_DAILY_PUSH_HOUR",   6))
NEWS_DAILY_PUSH_MINUTE: int = int(os.getenv("NEWS_DAILY_PUSH_MINUTE", 30))

_push_ids_raw = os.getenv("NEWS_DAILY_PUSH_USER_IDS", "")
if _push_ids_raw.strip():
    NEWS_DAILY_PUSH_USER_IDS: list[int] = [
        int(x) for x in _push_ids_raw.split(",") if x.strip()
    ]
else:
    NEWS_DAILY_PUSH_USER_IDS: list[int] = list(ALLOWED_USER_IDS)

# ── Admin Alert ───────────────────────────────────────────────────────────────
# Telegram-Chat-ID fuer System-Alerts (Feed-Health, Fehler etc.)
# Setzen via .env: ADMIN_CHAT_ID=123456789
# Fallback: erster Eintrag aus ALLOWED_USER_IDS (meist der Bot-Owner)
_admin_raw = os.getenv("ADMIN_CHAT_ID", "")
if _admin_raw.strip():
    ADMIN_CHAT_ID: int | None = int(_admin_raw.strip())
else:
    ADMIN_CHAT_ID: int | None = next(iter(ALLOWED_USER_IDS), None)

# ── Google Sheets / Dienstplan ────────────────────────────────────────────────
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

SCHEDULE_URLAUB_SHEET_ID = os.getenv(
    "SCHEDULE_URLAUB_SHEET_ID", "1M9WTVPlP-ivvmW_SsPSzQLIHBDwnGdFyoqgeHR7QUZE"
)
SCHEDULE_WUNSCH_SHEET_ID = os.getenv(
    "SCHEDULE_WUNSCH_SHEET_ID", "1a1IcfdnfyU-MdLjzCajC2LH5thbHw5WTN9Q1O1kInt8"
)
SCHEDULE_KRANK_SHEET_ID = os.getenv(
    "SCHEDULE_KRANK_SHEET_ID", "1BO4LwgNm9YWHyrYqy6N9kaDgoj1pA7xpcYZVkWlNkLc"
)
SCHEDULE_OUTPUT_SHEET_ID = os.getenv(
    "SCHEDULE_OUTPUT_SHEET_ID", "1nMF24sf-HNvgJRMvQeZ3lTSf6qiGTvsHXedWjFpckkQ"
)


# ── TTS ──────────────────────────────────────────────────────────────────────
TTS_ENABLED: bool = os.getenv("TTS_ENABLED", "true").lower() == "true"
TTS_VOICE: str = os.getenv("TTS_VOICE", "de-DE-KatjaNeural")

# ── Session Summaries ─────────────────────────────────────────────────────────
SESSION_SUMMARY_HOUR: int = int(os.getenv("SESSION_SUMMARY_HOUR", 23))
SESSION_SUMMARY_MINUTE: int = int(os.getenv("SESSION_SUMMARY_MINUTE", 0))
SESSION_SUMMARY_MIN_MESSAGES: int = int(os.getenv("SESSION_SUMMARY_MIN_MESSAGES", 6))

# ── Health Check ──────────────────────────────────────────────────────────────
HEALTH_CHECK_HOUR: int = int(os.getenv("HEALTH_CHECK_HOUR", 7))
HEALTH_CHECK_MINUTE: int = int(os.getenv("HEALTH_CHECK_MINUTE", 0))

# ── Google Calendar ───────────────────────────────────────────────────────────
# Kalender-IDs = E-Mail-Adresse des jeweiligen Accounts (nach Kalender-Sharing)
GCAL_CALENDAR_ID_1: str = os.getenv("GCAL_CALENDAR_ID_1", "")
GCAL_CALENDAR_ID_2: str = os.getenv("GCAL_CALENDAR_ID_2", "")
GCAL_REMINDER_MINUTES: int = int(os.getenv("GCAL_REMINDER_MINUTES", "15"))
GCAL_CHECK_INTERVAL_MINUTES: int = int(os.getenv("GCAL_CHECK_INTERVAL_MINUTES", "5"))
GCAL_DAILY_SUMMARY_HOUR: int = int(os.getenv("GCAL_DAILY_SUMMARY_HOUR", "6"))
GCAL_DAILY_SUMMARY_MINUTE: int = int(os.getenv("GCAL_DAILY_SUMMARY_MINUTE", "30"))


# ── Trading Bot (Freqtrade / Crypto) ─────────────────────────────────────────
FREQTRADE_API_URL:      str = os.getenv("FREQTRADE_API_URL", "http://localhost:8080")
FREQTRADE_API_USERNAME: str = os.getenv("FREQTRADE_API_USERNAME", "admin")
FREQTRADE_API_PASSWORD: str = os.getenv("FREQTRADE_API_PASSWORD", "")
TRADING_STATS_HOUR:     int = int(os.getenv("TRADING_STATS_HOUR", "8"))
TRADING_STATS_MINUTE:   int = int(os.getenv("TRADING_STATS_MINUTE", "15"))

# ── Alpaca (US-Aktien) ────────────────────────────────────────────────────────
ALPACA_API_KEY:    str  = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY: str  = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER:      bool = os.getenv("ALPACA_PAPER", "false").lower() == "true"
ALPACA_STAKE_USD:  float = float(os.getenv("ALPACA_STAKE_USD", "50"))
ALPACA_SCAN_HOUR:  int  = int(os.getenv("ALPACA_SCAN_HOUR", "16"))   # 16:00 CEST = 10:00 ET
ALPACA_SCAN_MINUTE: int = int(os.getenv("ALPACA_SCAN_MINUTE", "0"))

# ── Lead Qualifying Agent ─────────────────────────────────────────────────────
SERP_API_KEY: str = os.getenv("SERP_API_KEY", "")
NORTHDATA_API_KEY: str = os.getenv("NORTHDATA_API_KEY", "")
LEAD_QUALIFYING_HOUR: int = int(os.getenv("LEAD_QUALIFYING_HOUR", "8"))
LEAD_QUALIFYING_MINUTE: int = int(os.getenv("LEAD_QUALIFYING_MINUTE", "0"))


def validate_config():
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not OPENROUTER_API_KEY:
        missing.append("OPENROUTER_API_KEY")
    if missing:
        raise ValueError(f"Fehlende Umgebungsvariablen: {', '.join(missing)}")
