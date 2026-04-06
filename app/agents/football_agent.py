import logging
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Du bist ein Fußball-Experte. Beantworte alle Fragen rund um Fußball: "
    "Spieler, Vereine, Ligen, Ergebnisse, Statistiken und Transfers. "
    "Antworte auf Deutsch, kurz und präzise. "
    "Wenn du aktuelle Ergebnisse nicht kennst, weise darauf hin dass deine Daten ein Ablaufdatum haben."
)


async def handle_text(text: str, history: list = None) -> str:
    response = await ask_llm(text, history=history or [], system_prompt=SYSTEM_PROMPT)
    return response