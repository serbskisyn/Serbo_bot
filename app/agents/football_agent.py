import logging
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Fussball-Experte. Deutsch. Praezise, kein Fuelltext, direkt zum Punkt. "
    "Spieler, Vereine, Ligen, Ergebnisse, Statistiken, Transfers, Taktik. "
    "Falls Web-Kontext vorhanden: priorisiere diese Infos als aktuellste Quelle. "
    "Kein Aktualitaetshinweis wenn Web-Kontext vorliegt."
)


async def handle_text(text: str, history: list = None, web_context: str = "") -> str:
    prompt = text
    if web_context:
        prompt = f"{web_context}Frage: {text}"
    return await ask_llm(prompt, history=history or [], system_prompt=SYSTEM_PROMPT)
