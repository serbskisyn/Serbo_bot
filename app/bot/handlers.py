import io
import logging
from telegram import Update
from telegram.ext import ContextTypes
from app.services.openrouter_client import extract_facts
from app.services.speech_to_text import transcribe_voice
from app.security.injection_guard import is_injection_async
from app.security.rate_limiter import is_rate_limited
from app.bot.conversation import get_history, add_message, clear_history
from app.bot.memory import add_direct, add_indirect, get_memory_prompt, clear_memory, format_memory_overview
from app.bot.whitelist import is_allowed
from app.agents.runner import run as agent_run
from app.agents.football_news_agent import fetch_news_for_user
from app.bot.schedule_dialog import get_schedule_handler
from app.bot.debug_handler import get_debug_handler

logger = logging.getLogger(__name__)


def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Splittet langen Text an Zeilenumbruechen unter dem Limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    current = []
    current_len = 0
    for line in text.split("\n"):
        if current_len + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


async def _process_message(user_id: int, text: str, update: Update, context) -> None:
    history = get_history(user_id)
    result = await agent_run(user_id, text, history)

    if isinstance(result, dict) and result.get("response") == "__CHART__":
        png_bytes = result.get("chart_bytes")
        add_message(user_id, "user", text)
        add_message(user_id, "assistant", "[Chart generiert]")
        await update.message.reply_photo(photo=io.BytesIO(png_bytes))
        return

    response = result if isinstance(result, str) else result.get("response", "")
    add_message(user_id, "user", text)
    add_message(user_id, "assistant", response)
    facts = await extract_facts(text, response)
    for key, value in facts.get("direct", {}).items():
        add_direct(user_id, key, value)
    for fact in facts.get("indirect", []):
        add_indirect(user_id, fact)
    await update.message.reply_text(response)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    clear_history(user.id)
    await update.message.reply_text(
        f"Hallo {user.first_name}! 👋\n"
        f"Ich bin dein KI-Assistent. Schreib mir einfach eine Nachricht.\n\n"
        f"/reset — Gesprächsverlauf löschen\n"
        f"/memory — Was ich über dich weiß\n"
        f"/forget — Mein Gedächtnis löschen\n"
        f"/news — Aktuelle News deiner Lieblingsclubs\n"
        f"/news fresh — News sofort neu laden (Live-Fetch)\n"
        f"/dienstplan — Dienstplan erstellen\n"
        f"/debugwunsch — Sheet-Struktur prüfen (Diagnose)"
    )


async def reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    clear_history(user_id)
    await update.message.reply_text("🗑️ Gesprächsverlauf gelöscht.")


async def memory_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    overview = format_memory_overview(user_id)
    await update.message.reply_text(overview)


async def forget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    clear_memory(user_id)
    await update.message.reply_text("🧹 Gedächtnis gelöscht.")


async def news_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    args = context.args or []
    force_refresh = any(a.lower() == "fresh" for a in args)

    if force_refresh:
        await update.message.reply_text("🔄 Lade News live neu — einen Moment…")
    else:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # fetch_news_for_user gibt list[str] zurück — ein Block pro Verein
    blocks: list[str] = await fetch_news_for_user(user_id, force_refresh=force_refresh)

    for block in blocks:
        for chunk in _split_message(block):
            await update.message.reply_text(
                chunk,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    logger.info("Textnachricht von User %d", user_id)

    if not is_allowed(user_id):
        logger.warning("Unauthorized user | user=%d", user_id)
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    limited, retry_after = is_rate_limited(user_id)
    if limited:
        logger.warning("Rate limit exceeded | user=%d", user_id)
        await update.message.reply_text(f"⏳ Zu viele Nachrichten. Bitte {retry_after}s warten.")
        return

    if await is_injection_async(user_text):
        logger.warning("Injection attempt | user=%d", user_id)
        await update.message.reply_text("⚠️ Ungültige Eingabe erkannt.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await _process_message(user_id, user_text, update, context)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info("Sprachnachricht von User %d", user_id)

    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    limited, retry_after = is_rate_limited(user_id)
    if limited:
        await update.message.reply_text(f"⏳ Zu viele Nachrichten. Bitte {retry_after}s warten.")
        return

    voice = update.message.voice
    voice_file = await context.bot.get_file(voice.file_id)
    voice_bytes = await voice_file.download_as_bytearray()

    try:
        text = await transcribe_voice(bytes(voice_bytes))
    except Exception as e:
        logger.exception("Transkription fehlgeschlagen")
        await update.message.reply_text(f"❌ Transkription fehlgeschlagen: {e}")
        return

    await update.message.reply_text(f"🎤 Transkribiert: _{text}_", parse_mode="Markdown")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await _process_message(user_id, text, update, context)


def register_handlers(application):
    from telegram.ext import CommandHandler, MessageHandler, filters
    application.add_handler(get_schedule_handler())
    application.add_handler(get_debug_handler())          # /debugwunsch
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("reset", reset_handler))
    application.add_handler(CommandHandler("memory", memory_handler))
    application.add_handler(CommandHandler("forget", forget_handler))
    application.add_handler(CommandHandler("news", news_handler))
    application.add_handler(MessageHandler(filters.VOICE, voice_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

async def error_handler(update, context):
    import logging
    logging.getLogger(__name__).error("Error: %s", context.error, exc_info=context.error)
