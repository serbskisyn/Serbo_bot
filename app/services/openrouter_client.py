import httpx
import logging
from app.config import OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

async def ask_llm(
    user_text: str,
    system_prompt: str = "Du bist ein hilfreicher Assistent. Antworte auf Deutsch.",
) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

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
            data = response.json()
            return data["choices"][0]["message"]["content"]

    except httpx.TimeoutException:
        logger.error("OpenRouter Timeout")
        return "Die Anfrage hat zu lange gedauert. Bitte versuche es erneut."

    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter Fehler: {e.response.status_code}")
        return "Problem mit der KI-Verbindung. Bitte versuche es später erneut."

    except Exception as e:
        logger.error(f"Unbekannter Fehler: {e}", exc_info=True)
        return "Ein unerwarteter Fehler ist aufgetreten."
