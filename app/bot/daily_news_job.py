"""
Täglicher News-Push via Telegram JobQueue.
Läuft jeden Morgen um NEWS_DAILY_PUSH_HOUR:NEWS_DAILY_PUSH_MINUTE (Europe/Berlin).
Schickt die News nur an den ersten User aus NEWS_DAILY_PUSH_USER_IDS.
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

BERLIN_OFFSET = timedelta(hours=2)  # CEST (Sommer); Winter: hours=1
CEST = timezone(BERLIN_OFFSET)


async def _send_daily_news(context) -> None:
    """Callback der JobQueue: sendet News an den ersten konfigurierten User."""
    if not NEWS_DAILY_PUSH_USER_IDS:
        logger.warning("Daily News Push: keine User-IDs konfiguriert.")
        return

    user_id = NEWS_DAILY_PUSH_USER_IDS[0]
    bot = context.bot
    logger.info("Daily News Push gestartet | user=%d", user_id)

    try:
        # fetch_news_for_user gibt list[str] zurück — ein Block pro Verein
        blocks: list[str] = await fetch_news_for_user(user_id, force_refresh=False)
        total_chunks = 0
        for block in blocks:
            for chunk in _split(block):
                await bot.send_message(
                    chat_id=user_id,
                    text=chunk,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
                total_chunks += 1
        logger.info("Daily News Push gesendet | user=%d | chunks=%d", user_id, total_chunks)
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
    Falls job_queue nicht verfügbar ist (fehlendes [job-queue] Extra),
    wird nur eine Warnung geloggt — der Bot startet trotzdem.
    """
    if not NEWS_DAILY_PUSH_USER_IDS:
        logger.info("NEWS_DAILY_PUSH_USER_IDS leer — kein Daily Push registriert.")
        return

    if app.job_queue is None:
        logger.warning(
            "JobQueue nicht verfügbar (pip install 'python-telegram-bot[job-queue]' fehlt). "
            "Daily News Push deaktiviert — Bot läuft trotzdem."
        )
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
        "Daily News Push registriert: täglich %02d:%02d CEST — user=%d",
        NEWS_DAILY_PUSH_HOUR,
        NEWS_DAILY_PUSH_MINUTE,
        NEWS_DAILY_PUSH_USER_IDS[0],
    )
