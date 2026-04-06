import logging
from app.agents.state import BotState
from app.services.web_search import search, format_results
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Du bist ein hilfreicher Assistent mit Zugriff auf aktuelle Websuche. "
    "Nutze die bereitgestellten Suchergebnisse um die Frage zu beantworten. "
    "Antworte auf Deutsch, kurz und präzise. "
    "Gib am Ende die relevantesten Quellen als Liste an."
)


async def web_node(state: BotState) -> BotState:
    query = state["text"]
    logger.info(f"Web Node -> Suche: '{query}' | user={state['user_id']}")

    results = await search(query)
    context = format_results(results)

    prompt = (
        f"Suchanfrage: {query}\n\n"
        f"Suchergebnisse:\n{context}\n\n"
        f"Beantworte die Anfrage basierend auf diesen Ergebnissen."
    )

    response = await ask_llm(
        prompt,
        history=state.get("messages", []),
        system_prompt=SYSTEM_PROMPT
    )

    logger.info(f"Web Node -> Antwort generiert | user={state['user_id']}")
    return {**state, "response": response}
