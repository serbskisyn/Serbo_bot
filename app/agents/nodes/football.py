import logging
from app.agents.state import BotState
from app.agents.football_agent import handle_text
from app.services.web_search import search, format_results
from app.bot.memory import get_confirmed

logger = logging.getLogger(__name__)

# Keywords die Live-Daten erfordern -> Web-Search wird zugeschaltet
LIVE_KEYWORDS = [
    "kader", "aufstellung", "spieltag", "spielplan", "ergebnis", "ergebnisse",
    "tabelle", "tabellenstand", "heute", "aktuell", "aktueller", "aktuelles",
    "wann", "gegen wen", "nächstes spiel", "letztes spiel", "transfer",
    "verletzt", "verletzung", "gesperrt", "trainer", "formation", "taktik",
    "saison", "liga", "bundesliga", "2. bundesliga", "3. liga",
]


def _needs_live_data(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in LIVE_KEYWORDS)


def _get_club_from_memory(user_id: int) -> str:
    """Liest Lieblingsverein aus User-Memory fuer gezielten Web-Query."""
    memory = get_confirmed(user_id)
    for key in ("lieblingsverein", "verein", "club", "fussballverein"):
        val = memory.get(key, "")
        if val:
            # Klammern entfernen: 'Borussia Dortmund (BVB 09)' -> 'Borussia Dortmund'
            import re
            return re.sub(r"\(.*?\)", "", val).strip()
    return ""


async def football_node(state: BotState) -> BotState:
    text    = state["text"]
    user_id = state["user_id"]
    history = state.get("messages", [])

    web_context = ""

    if _needs_live_data(text):
        # Club aus Memory holen fuer praeziseren Query
        club = _get_club_from_memory(user_id)
        query = f"{club} {text}".strip() if club else text

        logger.info("Football Node -> Live-Daten benoetigt | query='%s' | user=%d", query, user_id)
        try:
            results = await search(query)
            if results:
                web_context = (
                    "Aktuelle Web-Informationen (Stand heute):\n"
                    + format_results(results)
                    + "\n\n"
                )
                logger.info("Football Node -> Web-Ergebnisse erhalten: %d Treffer", len(results))
        except Exception as e:
            logger.warning("Football Node -> Web-Search fehlgeschlagen: %s", e)

    response = await handle_text(text, history=history, web_context=web_context)
    logger.info("Football Node -> Antwort generiert | user=%d", user_id)
    return {**state, "response": response}
