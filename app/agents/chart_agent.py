import logging
from telegram import Update
from app.services.openrouter_client import ask_llm
from app.bot.conversation import get_history, add_message

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Du bist ein Datenvisualisierungs-Experte. "
    "Wenn der Nutzer einen Chart oder eine Grafik anfordert, beschreibe klar wie sie aussehen würde "
    "und gib Python-Code mit matplotlib oder plotly aus, den der Nutzer direkt ausführen kann. "
    "Antworte auf Deutsch, strukturiert und präzise."
)


async def handle(user_id: int, text: str, update: Update) -> None:
    history = get_history(user_id)
    response = await ask_llm(text, history=history, system_prompt=SYSTEM_PROMPT)
    add_message(user_id, "user", text)
    add_message(user_id, "assistant", response)
    await update.message.reply_text(response)
