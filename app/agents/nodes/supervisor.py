import json
import logging
from app.agents.state import BotState
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

# Schwelle unter der der vorherige Topic beibehalten wird
CONFIDENCE_THRESHOLD = 0.60

VALID_AGENTS = {"general", "football", "chart", "web"}

ROUTING_PROMPT = """Routing-Agent. Antworte NUR mit JSON, kein Text drumherum:
{"agent": "<agent>", "confidence": <0.0-1.0>}

Agenten:
- football: Alles mit Fussball-Bezug: Spieler, Vereine, Ligen, Transfers, Ergebnisse,
  Kader, Taktik, Trainer, Tabelle, Tabellenstand, Tabellenposition, Spieltag,
  Aufstellung, Verletzung, Tor, Bundesliga, Champions League, DFB-Pokal.
  WICHTIG: Wenn ein Vereinsname (z.B. Dynamo Dresden, Bayern, BVB, Dortmund) vorkommt
  und gleichzeitig ein Fussball-Begriff (Tabelle, Platz, Punkte, Kader, Spiel,
  Ergebnis, Trainer, Transfer) -> immer football, auch wenn 'aktuell' oder
  'heute' im Text steht.
- chart: Diagramme, Grafiken, Plots, Visualisierungen
- web: Allgemeine aktuelle News, Wetter, Preise, heutige Ereignisse OHNE Fussball-Bezug.
  NIEMALS web wenn ein Verein + Fussball-Begriff vorkommt.
- general: alles andere, Small Talk, allgemeine Fragen

Prioritaet: football > web wenn Fussball-Kontext erkennbar.

Confidence-Regeln:
- Eindeutiger Intent -> 0.9+
- Kurze Folge-Antwort ohne klaren Kontext ("ja", "mehr dazu", "und?") -> 0.2-0.4
- Thema erkennbar aber nicht sicher -> 0.5-0.7"""


async def supervisor_node(state: BotState) -> BotState:
    response = await ask_llm(
        state["text"],
        history=[],
        system_prompt=ROUTING_PROMPT
    )

    # JSON parsen
    agent      = "general"
    confidence = 0.5
    try:
        # Robustes Parsen: manchmal kommt ```json ... ``` drum herum
        raw = response.strip().strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
        data       = json.loads(raw)
        agent      = data.get("agent", "general").strip().lower()
        confidence = float(data.get("confidence", 0.5))
    except Exception as e:
        logger.warning("Supervisor JSON-Parse-Fehler: %s | raw='%s'", e, response[:80])

    if agent not in VALID_AGENTS:
        logger.warning("Supervisor -> ungueltiges Routing '%s', fallback general", agent)
        agent = "general"

    # Sicherheitsnetz: enthaelt der Text einen Vereinsnamen + Fussball-Begriff
    # -> immer football, egal was der LLM sagt
    CLUB_NAMES = [
        "dynamo", "dresden", "bvb", "dortmund", "bayern", "leipzig", "leverkusen",
        "frankfurt", "stuttgart", "freiburg", "schalke", "hamburg", "berlin",
        "gladbach", "werder", "bremen", "koeln", "koln", "hoffenheim", "augsburg",
        "mainz", "bochum", "wolfsburg", "hertha", "real madrid", "barcelona",
        "liverpool", "chelsea", "arsenal", "manchester", "juventus", "milan",
        "inter", "psg", "paris",
    ]
    FOOTBALL_TERMS = [
        "tabelle", "tabellenstand", "tabellenposition", "platz", "punkte",
        "kader", "aufstellung", "spieltag", "spielplan", "ergebnis", "ergebnisse",
        "transfer", "verletzt", "verletzung", "trainer", "formation", "taktik",
        "tor", "tore", "bundesliga", "champions league", "dfb", "liga",
        "saison", "abstieg", "aufstieg", "meister",
    ]
    low = state["text"].lower()
    has_club    = any(c in low for c in CLUB_NAMES)
    has_fb_term = any(t in low for t in FOOTBALL_TERMS)
    if has_club and has_fb_term and agent != "football":
        logger.info(
            "Supervisor -> Sicherheitsnetz greift: %s -> football | user=%d",
            agent, state["user_id"]
        )
        agent      = "football"
        confidence = 0.9

    # Topic-Carry: bei niedriger Confidence vorherigen stabilen Topic verwenden
    prev_topic = state.get("topic", "")
    if confidence < CONFIDENCE_THRESHOLD and prev_topic in VALID_AGENTS:
        logger.info(
            "Supervisor -> Confidence %.2f < %.2f | Topic-Carry: %s -> %s | user=%d",
            confidence, CONFIDENCE_THRESHOLD, agent, prev_topic, state["user_id"]
        )
        agent = prev_topic
    else:
        logger.info(
            "Supervisor -> %s (confidence=%.2f) | user=%d",
            agent, confidence, state["user_id"]
        )

    # Stabilen Topic nur bei hoher Confidence aktualisieren
    new_topic = agent if confidence >= CONFIDENCE_THRESHOLD else prev_topic

    return {**state, "agent": agent, "topic": new_topic, "confidence": confidence}
