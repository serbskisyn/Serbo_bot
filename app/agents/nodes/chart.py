import logging
from app.agents.state import BotState
from app.agents.chart_agent import generate_chart

logger = logging.getLogger(__name__)


async def chart_node(state: BotState) -> BotState:
    png_bytes = await generate_chart(state["text"])

    if png_bytes:
        logger.info(f"Chart Node -> PNG generiert | user={state['user_id']}")
        return {**state, "response": "__CHART__", "chart_bytes": png_bytes}
    else:
        logger.warning(f"Chart Node -> Generierung fehlgeschlagen | user={state['user_id']}")
        return {**state, "response": "❌ Chart konnte nicht erstellt werden. Bitte präzisiere deine Anfrage."}