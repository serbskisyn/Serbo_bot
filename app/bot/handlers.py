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
        f"/news fresh — News sofort neu laden (Live-Fetch)"
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

    # Argument-Parsing: /news [fresh]
    args = context.args or []
    force_refresh = any(a.lower() == "fresh" for a in args)

    if force_refresh:
        await update.message.reply_text("🔄 Lade News live neu — einen Moment…")
    else:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    result = await fetch_news_for_user(user_id, force_refresh=force_refresh)

    for chunk in _split_message(result):
        await update.message.reply_text(chunk, parse_mode="Markdown", disable_web_page_preview=True)


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
        logger.warning("Injection blocked (text) | user=%d | text=%s", user_id, user_text[:60])
        await update.message.reply_text("⚠️ Deine Nachricht wurde aus Sicherheitsgründen blockiert.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await _process_message(user_id, user_text, update, context)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info("Sprachnachricht von User %d", user_id)

    if not is_allowed(user_id):
        logger.warning("Unauthorized user | user=%d", user_id)
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    limited, retry_after = is_rate_limited(user_id)
    if limited:
        logger.warning("Rate limit exceeded | user=%d", user_id)
        await update.message.reply_text(f"⏳ Zu viele Nachrichten. Bitte {retry_after}s warten.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    voice_file = await context.bot.get_file(update.message.voice.file_id)
    ogg_bytes = await voice_file.download_as_bytearray()
    transcript = await transcribe_voice(bytes(ogg_bytes))

    if not transcript:
        await update.message.reply_text("Ich konnte die Sprachnachricht leider nicht verstehen.")
        return

    if await is_injection_async(transcript):
        logger.warning("Injection blocked (voice) | user=%d | transcript=%s", user_id, transcript[:60])
        await update.message.reply_text("⚠️ Deine Nachricht wurde aus Sicherheitsgründen blockiert.")
        return

    await update.message.reply_text(f"🎤 Ich habe verstanden: _{transcript}_", parse_mode="Markdown")
    await _process_message(user_id, transcript, update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Fehler: %s", context.error, exc_info=context.error)
