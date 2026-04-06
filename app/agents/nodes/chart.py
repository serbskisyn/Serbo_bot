import logging
from app.agents.state import BotState
from app.agents import chart_agent

logger = logging.getLogger(__name__)


async def chart_node(state: BotState) -> BotState:
    response = await chart_agent.handle_text(state["text"])
    logger.info(f"Chart Node -> Antwort generiert | user={state['user_id']}")
    return {**state, "response": response}
