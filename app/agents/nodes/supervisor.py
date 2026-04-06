import logging
from app.agents.state import BotState
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

ROUTING_PROMPT = """Du bist ein Routing-Agent. Analysiere die Nutzeranfrage und antworte NUR mit einem dieser Wörter:
- general
- football
- chart
- web

Regeln:
- football: alles rund um Fußball — Spieler, Vereine, Ligen, Transfers, Ergebnisse, Statistiken, Trainer
- chart: Diagramme, Grafiken, Visualisierungen, Plots
- web: aktuelle Nachrichten, Wetter, Live-Daten, Preise, aktuelle Ereignisse
- general: alles andere

Antworte NUR mit dem einen Wort. Keine Erklärung."""

VALID_AGENTS = {"general", "football", "chart", "web"}


async def supervisor_node(state: BotState) -> BotState:
    response = await ask_llm(
        state["text"],
        history=[],
        system_prompt=ROUTING_PROMPT
    )
    agent = response.strip().lower()

    if agent not in VALID_AGENTS:
        logger.warning(f"Supervisor -> ungültiges Routing '{agent}', fallback zu general")
        agent = "general"

    logger.info(f"Supervisor -> {agent} | user={state['user_id']}")
    return {**state, "agent": agent}