import logging
from app.agents.state import BotState
from app.services.openrouter_client import ask_llm
from app.bot.memory import get_memory_prompt
from app.services.notes_index import recall, recall_block
from app.services.proactive_context import get_proactive_context

logger = logging.getLogger(__name__)


async def general_node(state: BotState) -> BotState:
    user_id = state["user_id"]
    memory_context = get_memory_prompt(user_id)
    recalled = recall_block(await recall(user_id, state["text"]))
    proactive = await get_proactive_context(user_id)
    system_prompt = (
        "Hilfreicher Assistent. Deutsch. Präzise, kein Fülltext, direkt zum Punkt. "
        "Bullet Points bevorzugen. Keine Einleitungssätze. Fragmente OK. "
        f"{memory_context}{recalled}{proactive}"
    )
    response = await ask_llm(
        state["text"],
        history=state.get("messages", []),
        system_prompt=system_prompt
    )
    logger.info(f"General Node -> Antwort generiert | user={state['user_id']}")
    return {**state, "response": response}
