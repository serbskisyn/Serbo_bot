import logging
from app.agents.state import BotState
from app.agents import football_agent

logger = logging.getLogger(__name__)


async def football_node(state: BotState) -> BotState:
    response = await football_agent.handle_text(state["text"])
    logger.info(f"Football Node -> Antwort generiert | user={state['user_id']}")
    return {**state, "response": response}
