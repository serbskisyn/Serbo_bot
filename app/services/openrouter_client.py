import re
import json
import httpx
import logging
from app.config import OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
EXTRACTOR_MODEL = "anthropic/claude-haiku-4"

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
) -> str:
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1024,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    except httpx.TimeoutException:
        logger.error("OpenRouter Timeout")
        return "Die Anfrage hat zu lange gedauert. Bitte versuche es erneut."
    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter Fehler: {e.response.status_code}")
        return "Problem mit der KI-Verbindung. Bitte versuche es später erneut."
    except Exception as e:
        logger.error(f"Unbekannter Fehler: {e}", exc_info=True)
        return "Ein unerwarteter Fehler ist aufgetreten."


async def extract_facts(user_text: str, assistant_response: str) -> dict:
    messages = [
        {"role": "system", "content": EXTRACTOR_PROMPT},
        {"role": "user", "content": f"User: {user_text}\nAssistent: {assistant_response}"}
    ]
    payload = {
        "model": EXTRACTOR_MODEL,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 256,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return {"direct": {}, "indirect": []}
            return json.loads(match.group())

    except Exception as e:
        logger.warning(f"Fakten-Extraktion fehlgeschlagen: {e}")
        return {"direct": {}, "indirect": []}