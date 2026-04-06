import logging
from telegram import Update
from telegram.ext import ContextTypes
from app.services.openrouter_client import extract_facts
from app.services.speech_to_text import transcribe_voice
from app.security.injection_guard import is_injection_async
from app.bot.conversation import get_history, add_message, clear_history
from app.bot.memory import add_direct, add_indirect, get_memory_prompt, clear_memory, format_memory_overview
from app.agents.runner import run as agent_run

logger = logging.getLogger(__name__)


async def _process_message(user_id: int, text: str, update: Update, context) -> None:
    history = get_history(user_id)
    response = await agent_run(user_id, text, history)
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
    clear_history(user.id)
    await update.message.reply_text(
        f"Hallo {user.first_name}! 👋\n"
        f"Ich bin dein KI-Assistent. Schreib mir einfach eine Nachricht.\n\n"
        f"/reset — Gesprächsverlauf löschen\n"
        f"/memory — Was ich über dich weiß\n"
        f"/forget — Mein Gedächtnis löschen"
    )


async def reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("🗑️ Gesprächsverlauf gelöscht.")


async def memory_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    overview = format_memory_overview(update.effective_user.id)
    await update.message.reply_text(overview)


async def forget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_memory(update.effective_user.id)
    await update.message.reply_text("🧹 Gedächtnis gelöscht.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    user_id = update.effective_user.id
    logger.info(f"Textnachricht von User {user_id}")

    if await is_injection_async(user_text):
        logger.warning(f"Injection blocked (text) | user={user_id} | text={user_text[:60]}")
        await update.message.reply_text("⚠️ Deine Nachricht wurde aus Sicherheitsgründen blockiert.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await _process_message(user_id, user_text, update, context)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"Sprachnachricht von User {user_id}")

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    voice_file = await context.bot.get_file(update.message.voice.file_id)
    ogg_bytes = await voice_file.download_as_bytearray()
    transcript = await transcribe_voice(bytes(ogg_bytes))

    if not transcript:
        await update.message.reply_text("Ich konnte die Sprachnachricht leider nicht verstehen.")
        return

    if await is_injection_async(transcript):
        logger.warning(f"Injection blocked (voice) | user={user_id} | transcript={transcript[:60]}")
        await update.message.reply_text("⚠️ Deine Nachricht wurde aus Sicherheitsgründen blockiert.")
        return

    await update.message.reply_text(f"🎙️ Ich habe verstanden: _{transcript}_", parse_mode="Markdown")
    await _process_message(user_id, transcript, update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Fehler: {context.error}", exc_info=context.error)
