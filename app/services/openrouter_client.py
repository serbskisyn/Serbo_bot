import re
import json
import httpx
import logging
from app.config import OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
EXTRACTOR_MODEL = "openai/gpt-4o-mini"

EXTRACTOR_PROMPT = """Du analysierst eine Konversation und extrahierst Fakten über den User.
Antworte NUR mit validem JSON in diesem Format:
{
  "direct": {"schlüssel": "wert"},
  "indirect": ["fakt: wert", "fakt: wert"]
}
- "direct": Fakten die der User explizit über sich selbst gesagt hat (z.B. "Ich bin Bayern Fan", "Ich heiße Benno")
- "indirect": Themen die der User erwähnt hat aber nicht explizit als persönlich deklariert hat
- Wenn nichts erkennbar: {"direct": {}, "indirect": []}
- Schlüssel auf Deutsch, kurz und präzise (z.B. "name", "wohnort", "lieblingsverein")"""


async def ask_llm(
    user_text: str,
    history: list[dict] = None,
    system_prompt: str = "Du bist ein hilfreicher Assistent. Antworte auf Deutsch.",
    model: str | None = None,
) -> str:
    from app.services.llm_client import chat as _llm_chat
    from app.config import LLM_CHEAP_MODEL

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    try:
        return await _llm_chat(
            messages, model=model or LLM_CHEAP_MODEL,
            temperature=0.7, max_tokens=1024, timeout=60.0,
        )

    except httpx.TimeoutException:
        logger.error("OpenRouter Timeout")
        return "Die Anfrage hat zu lange gedauert. Bitte versuche es erneut."
    except httpx.HTTPStatusError as e:
        body = (e.response.text or "")[:300]
        logger.error("OpenRouter Fehler %s: %s", e.response.status_code, body[:200])
        body_lc = body.lower()
        if "limit exceeded" in body_lc or "insufficient_quota" in body_lc:
            return "⚠️ OpenRouter-Konto-Limit erreicht — bitte Credits nachladen unter openrouter.ai/keys"
        if e.response.status_code == 401:
            return "⚠️ OpenRouter API-Key abgelehnt (401) — Key in .env prüfen."
        if e.response.status_code == 429:
            return "⏳ OpenRouter Rate-Limit aktiv — kurz warten und nochmal."
        return f"Problem mit der KI-Verbindung (HTTP {e.response.status_code}). Bitte versuche es später erneut."
    except Exception as e:
        logger.error(f"Unbekannter Fehler: {e}", exc_info=True)
        return "Ein unerwarteter Fehler ist aufgetreten."


async def extract_facts(user_text: str, assistant_response: str) -> dict:
    """Deprecated. Kept only for backwards compat with very old callers.

    Returns an empty payload; real extraction now happens through
    app.services.profile_learner.learn() which is called directly from
    handlers.py.
    """
    return {"direct": {}, "indirect": []}