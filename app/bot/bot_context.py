"""
bot_context.py — Globaler Bot-Handle fuer Hintergrund-Tasks.

Da news_cache.py als Background-Scheduler laeuft (ausserhalb des
Telegram-Handler-Kontexts), kann es nicht direkt auf `context.bot`
zugreifen. Diese Datei stellt einen einfachen globalen Slot bereit,
in den main.py den fertigen Bot eintraegt.

Verwendung:
    # In main.py nach Application.build():
    from app.bot.bot_context import set_bot
    set_bot(application.bot)

    # In news_cache.py:
    from app.bot.bot_context import get_bot
    bot = get_bot()
    if bot:
        await bot.send_message(chat_id=..., text=...)
"""
from telegram import Bot

_bot: Bot | None = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def get_bot() -> Bot | None:
    return _bot
