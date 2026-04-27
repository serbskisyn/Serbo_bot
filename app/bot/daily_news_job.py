"""
Täglicher News-Push via Telegram JobQueue.
Läuft jeden Morgen um NEWS_DAILY_PUSH_HOUR:NEWS_DAILY_PUSH_MINUTE (Europe/Berlin).
Schickt die Top-5-News aller gespeicherten Lieblingsvereine an jeden
User aus NEWS_DAILY_PUSH_USER_IDS.
"""
import logging
from datetime import time, timezone, timedelta

from telegram.ext import Application

from app.agents.football_news_agent import fetch_news_for_user
from app.config import (
    NEWS_DAILY_PUSH_HOUR,
    NEWS_DAILY_PUSH_MINUTE,
    NEWS_DAILY_PUSH_USER_IDS,
)

logger = logging.getLogger(__name__)

# Europe/Berlin = UTC+1 (Winter) / UTC+2 (Sommer)
# python-telegram-bot JobQueue akzeptiert time mit tzinfo
BERLIN_OFFSET = timedelta(hours=2)   # CEST (Sommer); Winter: hours=1
# Einfachste robuste Lösung: feste Offset-Zone
CEST = timezone(BERLIN_OFFSET)


async def _send_daily_news(context) -> None:
    """Callback der JobQueue: sendet News an alle konfigurierten User."""
    bot = context.bot
    logger.info("Daily News Push gestartet | user_ids=%s", NEWS_DAILY_PUSH_USER_IDS)

    for user_id in NEWS_DAILY_PUSH_USER_IDS:
        try:
            result = await fetch_news_for_user(user_id, force_refresh=False)

            # Splitten falls Telegram-Limit (4096 Zeichen) überschritten
            chunks = _split(result)
            for chunk in chunks:
                await bot.send_message(
                    chat_id=user_id,
                    text=chunk,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            logger.info("Daily News Push gesendet | user=%d | chunks=%d", user_id, len(chunks))
        except Exception as e:
            logger.error("Daily News Push fehlgeschlagen | user=%d | error=%s", user_id, e)


def _split(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, current, current_len = [], [], 0
    for line in text.split("\n"):
        if current_len + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, current_len = [line], len(line)
        else:
            current.append(line)
            current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def register_daily_news_job(app: Application) -> None:
    """
    Registriert den täglichen News-Push in der JobQueue.
    Muss nach app.build() aufgerufen werden (JobQueue ist dann verfügbar).
    """
    if not NEWS_DAILY_PUSH_USER_IDS:
        logger.info("NEWS_DAILY_PUSH_USER_IDS leer — kein Daily Push registriert.")
        return

    push_time = time(
        hour=NEWS_DAILY_PUSH_HOUR,
        minute=NEWS_DAILY_PUSH_MINUTE,
        second=0,
        tzinfo=CEST,
    )

    app.job_queue.run_daily(
        callback=_send_daily_news,
        time=push_time,
        name="daily_news_push",
    )
    logger.info(
        "Daily News Push registriert: täglich %02d:%02d CEST | user_ids=%s",
        NEWS_DAILY_PUSH_HOUR,
        NEWS_DAILY_PUSH_MINUTE,
        NEWS_DAILY_PUSH_USER_IDS,
    )
