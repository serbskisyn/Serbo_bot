import asyncio
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from app.config import validate_config, TELEGRAM_BOT_TOKEN
from app.utils.logging_setup import setup_logging
from app.bot.handlers import (
    start_handler, text_handler, voice_handler,
    error_handler, reset_handler, memory_handler, forget_handler,
    news_handler
)
from app.services.news_cache import start_background_scheduler


def main():
    setup_logging()
    logger = logging.getLogger(__name__)
    validate_config()
    logger.info("Konfiguration OK — Bot wird gestartet...")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("reset", reset_handler))
    app.add_handler(CommandHandler("memory", memory_handler))
    app.add_handler(CommandHandler("forget", forget_handler))
    app.add_handler(CommandHandler("news", news_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_error_handler(error_handler)

    # News Cache Scheduler als Background-Task starten
    async def post_init(application):
        logger.info("News-Cache Background-Scheduler wird gestartet...")
        start_background_scheduler()

    app.post_init = post_init

    logger.info("Bot läuft. Warte auf Nachrichten...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
