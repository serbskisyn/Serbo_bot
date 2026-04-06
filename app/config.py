import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
BOT_NAME = os.getenv("BOT_NAME", "MeinAgent")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

ALLOWED_USER_IDS: set[int] = set(
    int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()
)

# Rate Limiting
RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", 10))
RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", 60))


def validate_config():
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not OPENROUTER_API_KEY:
        missing.append("OPENROUTER_API_KEY")
    if missing:
        raise ValueError(f"Fehlende Umgebungsvariablen: {', '.join(missing)}")