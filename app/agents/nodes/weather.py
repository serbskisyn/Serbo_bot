"""
weather.py — LangGraph node: location-aware live weather.

Resolution order for the location:
  1. an explicit place in the query ("Wetter in München", "für Hamburg")
  2. the user's profile identity.location
  3. "Berlin" (hard default — user's home base)

Fetches real data from Open-Meteo (app/services/weather.py) and lets the
LLM phrase it naturally, grounded on the fetched numbers.
"""
from __future__ import annotations

import logging
import re

from app.agents.state import BotState
from app.bot import profile
from app.services.openrouter_client import ask_llm
from app.services.weather import get_weather, format_weather_context

logger = logging.getLogger(__name__)

_DEFAULT_LOCATION = "Berlin"

# "Wetter in München", "wie ist es für Hamburg", "Wetter Köln morgen"
_LOC_RE = re.compile(
    r"(?:in|für|fuer)\s+([A-ZÄÖÜ][\wäöüß.-]+(?:\s+[A-ZÄÖÜ][\wäöüß.-]+)?)",
)

_SYSTEM_PROMPT = (
    "Wetter-Assistent. Deutsch. Du bekommst echte, aktuelle Wetterdaten von Open-Meteo. "
    "Beantworte die Frage des Users präzise und natürlich auf Basis dieser Daten. "
    "Kein Fülltext, direkt zum Punkt. Nenne die Temperatur und ob Regen/Schnee zu erwarten ist. "
    "Wenn nach einem bestimmten Tag gefragt wird, beziehe dich auf die Vorhersage. "
    "Erfinde KEINE Zahlen — nutze nur die gelieferten Werte."
)


def _resolve_location(user_id: int, text: str) -> str:
    m = _LOC_RE.search(text)
    if m:
        candidate = m.group(1).strip()
        # Avoid matching generic words that happen to be capitalised
        if candidate.lower() not in ("der", "die", "das", "deutschland", "moment"):
            return candidate
    loc = (profile.get_section(user_id, "identity") or {}).get("location")
    if loc and str(loc).strip():
        return str(loc).strip()
    return _DEFAULT_LOCATION


async def weather_node(state: BotState) -> BotState:
    user_id = state["user_id"]
    text = state["text"]
    location = _resolve_location(user_id, text)
    logger.info("Weather Node -> '%s' | user=%d", location, user_id)

    w = await get_weather(location)
    if w is None:
        # Fall back to the home default if a parsed place failed to geocode
        if location != _DEFAULT_LOCATION:
            logger.info("Weather Node -> retry default '%s'", _DEFAULT_LOCATION)
            w = await get_weather(_DEFAULT_LOCATION)
        if w is None:
            return {
                **state,
                "response": f"Ich konnte gerade keine Wetterdaten für *{location}* abrufen. "
                            "Versuch es gleich nochmal oder nenne einen anderen Ort.",
            }

    context = format_weather_context(w)
    prompt = (
        f"Frage des Users: {text}\n\n"
        f"Aktuelle Wetterdaten:\n{context}\n\n"
        f"Beantworte die Frage auf Basis dieser Daten."
    )
    response = await ask_llm(prompt, history=state.get("messages", []), system_prompt=_SYSTEM_PROMPT)
    logger.info("Weather Node -> Antwort generiert | user=%d | loc=%s", user_id, w["location"])
    return {**state, "response": response}
