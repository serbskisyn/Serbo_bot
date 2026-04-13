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
- football: Fussball (Spieler, Vereine, Ligen, Transfers, Ergebnisse, Kader, Taktik, Trainer)
- chart: Diagramme, Grafiken, Plots, Visualisierungen
- web: aktuelle News, Wetter, Live-Daten, Preise, heutige Ereignisse
- general: alles andere, Small Talk, allgemeine Fragen

Confidence-Regeln:
- Eindeutiger Intent ("Wer hat die CL gewonnen?") -> 0.9+
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
        logger.warning("Supervisor -> ungültiges Routing '%s', fallback general", agent)
        agent = "general"

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
