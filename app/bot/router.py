import logging
from enum import Enum

logger = logging.getLogger(__name__)


class AgentType(Enum):
    GENERAL = "general"
    FOOTBALL = "football"
    CHART = "chart"
    WEB = "web"


FOOTBALL_KEYWORDS = [
    "fußball", "fussball", "bundesliga", "champions league", "uefa", "fifa",
    "tor", "tore", "spieler", "trainer", "verein", "stadion", "tabelle",
    "ergebnis", "spieltag", "transfer", "nationalmannschaft", "weltmeister",
    "em ", "wm ", "dfb", "premier league", "la liga", "serie a", "ligue 1",
    "liverpool", "barcelona", "real madrid", "bayern", "dortmund", "chelsea",
    "arsenal", "manchester", "juventus", "inter", "milan", "psg",
    "scored", "goal", "match", "league", "football", "soccer",
]

CHART_KEYWORDS = [
    "chart", "diagramm", "grafik", "visualisierung", "plot", "graph",
    "balkendiagramm", "liniendiagramm", "kreisdiagramm", "kurve",
    "zeig mir", "zeichne", "erstelle eine grafik", "visualisiere",
    "bar chart", "line chart", "pie chart", "histogram",
]

WEB_KEYWORDS = [
    "aktuell", "heute", "news", "nachrichten", "wetter", "suche",
    "was ist", "wer ist", "wo ist", "wann ist", "wie viel kostet",
    "preis von", "neueste", "gerade", "live", "breaking", "trending",
    "search", "google", "internet", "online", "website",
]


def route(text: str) -> AgentType:
    """Bestimmt den passenden Agenten anhand von Keywords im Text."""
    lower = text.lower()

    for keyword in CHART_KEYWORDS:
        if keyword in lower:
            logger.info(f"Router -> CHART (keyword: '{keyword}')")
            return AgentType.CHART

    for keyword in FOOTBALL_KEYWORDS:
        if keyword in lower:
            logger.info(f"Router -> FOOTBALL (keyword: '{keyword}')")
            return AgentType.FOOTBALL

    for keyword in WEB_KEYWORDS:
        if keyword in lower:
            logger.info(f"Router -> WEB (keyword: '{keyword}')")
            return AgentType.WEB

    logger.info("Router -> GENERAL (no keyword match)")
    return AgentType.GENERAL
