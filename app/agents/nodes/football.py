import logging
from app.agents.state import BotState
from app.agents.football_agent import handle_text

logger = logging.getLogger(__name__)


async def football_node(state: BotState) -> BotState:
    response = await handle_text(state["text"], history=state.get("messages", []))
    logger.info(f"Football Node -> Antwort generiert | user={state['user_id']}")
    return {**state, "response": response}