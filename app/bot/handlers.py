import logging
from telegram import Update
from telegram.ext import ContextTypes
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Hallo {user.first_name}! 👋\n"
        f"Ich bin dein KI-Assistent. Schreib mir einfach eine Nachricht."
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    logger.info(f"Textnachricht von User {update.effective_user.id}")

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    response = await ask_llm(user_text)
    await update.message.reply_text(response)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Fehler: {context.error}", exc_info=context.error)
from app.services.speech_to_text import transcribe_voice

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Sprachnachricht von User {update.effective_user.id}")

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    voice_file = await context.bot.get_file(update.message.voice.file_id)
    ogg_bytes = await voice_file.download_as_bytearray()

    transcript = await transcribe_voice(bytes(ogg_bytes))

    if not transcript:
        await update.message.reply_text(
            "Ich konnte die Sprachnachricht leider nicht verstehen. Bitte versuche es erneut."
        )
        return

    await update.message.reply_text(
        f"🎙️ Ich habe verstanden: _{transcript}_",
        parse_mode="Markdown"
    )

    response = await ask_llm(transcript)
    await update.message.reply_text(response)
