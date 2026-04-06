import logging
from langgraph.graph import StateGraph, END
from app.agents.state import BotState
from app.agents.nodes.supervisor import supervisor_node
from app.agents.nodes.general import general_node
from app.agents.nodes.football import football_node
from app.agents.nodes.chart import chart_node

logger = logging.getLogger(__name__)


def route_agent(state: BotState) -> str:
    return state["agent"]


def build_graph():
    graph = StateGraph(BotState)

    # Nodes registrieren
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("general", general_node)
    graph.add_node("football", football_node)
    graph.add_node("chart", chart_node)

    # Einstiegspunkt
    graph.set_entry_point("supervisor")

    # Supervisor routet conditional weiter
    graph.add_conditional_edges("supervisor", route_agent, {
        "general": "general",
        "football": "football",
        "chart": "chart",
    })

    # Alle Nodes enden nach ihrer Antwort
    graph.add_edge("general", END)
    graph.add_edge("football", END)
    graph.add_edge("chart", END)

    return graph
