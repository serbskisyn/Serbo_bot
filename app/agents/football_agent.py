import logging
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Fußball-Experte. Deutsch. Präzise, kein Fülltext, direkt zum Punkt. "
    "Spieler, Vereine, Ligen, Ergebnisse, Statistiken, Transfers. "
    "Aktualitätslimit offen ansprechen wenn nötig."
)


async def handle_text(text: str, history: list = None) -> str:
    return await ask_llm(text, history=history or [], system_prompt=SYSTEM_PROMPT)
