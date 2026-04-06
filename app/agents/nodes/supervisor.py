import logging
from app.agents.state import BotState
from app.bot.router import route

logger = logging.getLogger(__name__)


async def supervisor_node(state: BotState) -> BotState:
    agent = route(state["text"])
    logger.info(f"Supervisor -> {agent.value} | user={state['user_id']}")
    return {**state, "agent": agent.value}
