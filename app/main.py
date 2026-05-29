import logging
from datetime import time
from zoneinfo import ZoneInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from app.config import (
    validate_config, TELEGRAM_BOT_TOKEN,
    SESSION_SUMMARY_HOUR, SESSION_SUMMARY_MINUTE,
    HEALTH_CHECK_HOUR, HEALTH_CHECK_MINUTE,
    GCAL_DAILY_SUMMARY_HOUR, GCAL_DAILY_SUMMARY_MINUTE,
    GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2,
)
from app.utils.logging_setup import setup_logging
from app.bot.handlers import (
    start_handler, text_handler, voice_handler,
    error_handler, reset_handler, memory_handler, forget_handler,
    news_handler, strava_handler, claude_handler, claudex_handler,
    claudex_fertig_handler, nein_handler, health_handler,
    termine_handler, kalender1_handler, kalender2_handler,
    xnews_handler, tests_handler, leads_handler,
)
from app.bot.todo_commands import todo_handler
from app.services.news_cache import start_background_scheduler
from app.bot.schedule_dialog import get_schedule_handler
from app.bot.debug_handler import get_debug_handler
from app.bot.daily_news_job import register_daily_news_job
from app.bot.bot_context import set_bot
from app.bot.session_summary import create_daily_summaries, summary_handler
from app.services.health_check import send_daily_health_check
from app.bot.gcal_reminder_job import register_gcal_reminder_job, send_daily_calendar_summary
from app.agents.schedule.lead_qualifying_agent import register_lead_qualifying_job
from app.bot.trading_job import tradebot_handler, register_trading_stats_job, recap_handler
from app.bot.alpaca_job import register_alpaca_jobs
from app.bot.sync_jobs import register_sync_jobs
from app.bot.briefing_job import register_briefing_job, briefing_handler
from app.bot.evening_job import register_evening_job, reflect_handler
from app.bot.sweep_job import register_sweep_job

_BERLIN = ZoneInfo("Europe/Berlin")


async def _post_init(application) -> None:
    logger = logging.getLogger(__name__)
    set_bot(application.bot)
    logger.info("News-Cache Background-Scheduler wird gestartet...")
    start_background_scheduler()
    register_daily_news_job(application)

    jq = application.job_queue
    if jq is None:
        logger.warning("JobQueue nicht verfügbar — Health Check und Session Summary deaktiviert.")
        return

    jq.run_daily(
        callback=send_daily_health_check,
        time=time(hour=HEALTH_CHECK_HOUR, minute=HEALTH_CHECK_MINUTE, tzinfo=_BERLIN),
        name="daily_health_check",
    )
    logger.info("Daily Health Check registriert: %02d:%02d Europe/Berlin", HEALTH_CHECK_HOUR, HEALTH_CHECK_MINUTE)

    jq.run_daily(
        callback=create_daily_summaries,
        time=time(hour=SESSION_SUMMARY_HOUR, minute=SESSION_SUMMARY_MINUTE, tzinfo=_BERLIN),
        name="daily_session_summaries",
    )
    logger.info("Daily Session Summaries registriert: %02d:%02d Europe/Berlin", SESSION_SUMMARY_HOUR, SESSION_SUMMARY_MINUTE)

    register_gcal_reminder_job(application)
    register_lead_qualifying_job(application)
    register_trading_stats_job(application)
    register_alpaca_jobs(application)
    register_sync_jobs(application)
    register_briefing_job(application)
    register_evening_job(application)
    register_sweep_job(application)

    if GCAL_CALENDAR_ID_1 or GCAL_CALENDAR_ID_2:
        jq.run_daily(
            callback=send_daily_calendar_summary,
            time=time(hour=GCAL_DAILY_SUMMARY_HOUR, minute=GCAL_DAILY_SUMMARY_MINUTE, tzinfo=_BERLIN),
            name="daily_calendar_summary",
        )
        logger.info("Tages-Kalenderübersicht registriert: %02d:%02d Europe/Berlin", GCAL_DAILY_SUMMARY_HOUR, GCAL_DAILY_SUMMARY_MINUTE)


def main():
    setup_logging()
    logger = logging.getLogger(__name__)
    validate_config()
    logger.info("Konfiguration OK — Bot wird gestartet...")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(get_schedule_handler())
    app.add_handler(get_debug_handler())
    app.add_handler(CommandHandler("start",   start_handler))
    app.add_handler(CommandHandler("reset",   reset_handler))
    app.add_handler(CommandHandler("memory",  memory_handler))
    app.add_handler(CommandHandler("forget",  forget_handler))
    app.add_handler(CommandHandler("news",    news_handler))
    app.add_handler(CommandHandler("xnews",   xnews_handler))
    app.add_handler(CommandHandler("strava",  strava_handler))
    app.add_handler(CommandHandler("claude",  claude_handler))
    app.add_handler(CommandHandler("claudex", claudex_handler))
    app.add_handler(CommandHandler("fertig",  claudex_fertig_handler))
    app.add_handler(CommandHandler("nein",    nein_handler))
    app.add_handler(CommandHandler("health",    health_handler))
    app.add_handler(CommandHandler("check",     health_handler))
    app.add_handler(CommandHandler("tests",     tests_handler))
    app.add_handler(CommandHandler("termine",   termine_handler))
    app.add_handler(CommandHandler("kalender1", kalender1_handler))
    app.add_handler(CommandHandler("kalender2", kalender2_handler))
    app.add_handler(CommandHandler("tradebot", tradebot_handler))
    app.add_handler(CommandHandler("recap",    recap_handler))
    app.add_handler(CommandHandler("leads",    leads_handler))
    app.add_handler(CommandHandler("todo",     todo_handler))
    app.add_handler(CommandHandler("todos",    todo_handler))
    app.add_handler(CommandHandler("briefing", briefing_handler))
    app.add_handler(CommandHandler("reflect",  reflect_handler))
    app.add_handler(CommandHandler("summary",  summary_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_error_handler(error_handler)

    logger.info("Bot läuft. Warte auf Nachrichten...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
