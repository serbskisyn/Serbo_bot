import logging
from app.agents.state import BotState
from app.services.openrouter_client import ask_llm
from app.bot.memory import get_memory_prompt

logger = logging.getLogger(__name__)


async def general_node(state: BotState) -> BotState:
    memory_context = get_memory_prompt(state["user_id"])
    system_prompt = (
        "Du bist ein hilfreicher Assistent. Antworte auf Deutsch. "
        "Antworte immer so kurz und präzise wie möglich. "
        "Keine langen Erklärungen, keine Prosa, keine Einleitungssätze. "
        "Bullet Points oder direkte Antworten bevorzugen."
        f"{memory_context}"
    )
    response = await ask_llm(
        state["text"],
        history=state.get("messages", []),
        system_prompt=system_prompt
    )
    logger.info(f"General Node -> Antwort generiert | user={state['user_id']}")
    return {**state, "response": response}
