"""
Grok-Client via OpenRouter — nutzt xAI Live-Search auf X.com.

Zwei Pfade:
1. OpenRouter (Default): Modell wird mit `:online`-Suffix aufgerufen.
   OpenRouter aktiviert dann Grok's native X-Search-Tools
   (x_keyword_search etc.). Citations liegen in
   `choices[0].message.annotations[].url_citation`.

2. xAI direct (wenn GROK_API_KEY gesetzt): klassisches
   `search_parameters` mit `sources=[{type:x}]`. Citations
   kommen als Top-Level-`citations`-Array.

Beide Pfade liefern dieselbe Output-Shape:
{"text": str, "citations": list[str], "model": str, "raw": dict}
"""
from __future__ import annotations

import logging
from typing import Literal

import httpx

from app.config import GROK_API_KEY, GROK_BASE_URL, GROK_MODEL, OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "Du bist ein Recherche-Assistent mit Live-Zugriff auf X.com (Twitter). "
    "Beantworte die User-Frage auf Deutsch, indem du aktuelle X-Posts zum Thema "
    "zusammenfasst. Format pro Post EXAKT so:\n"
    "- **@handle**: 1–2 Sätze Kerninhalt. → https://x.com/handle/status/<id>\n"
    "Die URL ist Pflicht — keine Antwort ohne URLs. Max. 5 Posts, "
    "neueste zuerst. Markiere spekulative Aussagen mit '(Meinung)'. "
    "Keine Erfindungen — wenn du keine Posts findest, sag das klar."
)


def _extract_citations(data: dict) -> list[str]:
    """Citations je nach Pfad: xAI direct → top-level, OpenRouter :online → annotations."""
    direct = data.get("citations") or []
    if direct:
        return list(direct)
    msg = data["choices"][0]["message"]
    urls: list[str] = []
    for ann in msg.get("annotations") or []:
        if ann.get("type") == "url_citation":
            url = ann.get("url_citation", {}).get("url")
            if url and url not in urls:
                urls.append(url)
    return urls


async def grok_search(
    query: str,
    sources: list[Literal["x", "web", "news", "rss"]] | None = None,
    max_results: int = 10,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    temperature: float = 0.2,
) -> dict:
    """
    Liefert {"text": str, "citations": list[str], "model": str, "raw": dict | None}.
    Bei Fehlern: text enthält eine kurze Fehlermeldung, citations ist leer.
    """
    sources = sources or ["x"]
    use_xai_direct = bool(GROK_API_KEY)
    if use_xai_direct:
        url     = f"{GROK_BASE_URL.rstrip('/')}/chat/completions"
        api_key = GROK_API_KEY
        model   = GROK_MODEL
    else:
        # OpenRouter aktiviert Grok's natives X-Search-Tool via :online-Suffix.
        url     = "https://openrouter.ai/api/v1/chat/completions"
        api_key = OPENROUTER_API_KEY
        model   = GROK_MODEL if GROK_MODEL.endswith(":online") else f"{GROK_MODEL}:online"

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": query},
        ],
        "temperature": temperature,
        "max_tokens":  1024,
    }
    if use_xai_direct:
        # Nur direkter xAI-Pfad versteht search_parameters zuverlässig.
        payload["search_parameters"] = {
            "mode":               "on",
            "sources":            [{"type": s} for s in sources],
            "max_search_results": max_results,
            "return_citations":   True,
        }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("Grok HTTP %s: %s", e.response.status_code, e.response.text[:300])
        return {"text": f"❌ Grok-Fehler ({e.response.status_code}). Bitte später erneut.",
                "citations": [], "model": model, "raw": None}
    except httpx.TimeoutException:
        logger.warning("Grok timeout (60s) für: %s", query[:80])
        return {"text": "⏳ Grok hat zu lange gebraucht. Frag's nochmal.",
                "citations": [], "model": model, "raw": None}
    except Exception as e:
        logger.exception("Grok call failed: %s", e)
        return {"text": "❌ Grok-Verbindung fehlgeschlagen.",
                "citations": [], "model": model, "raw": None}

    text      = (data["choices"][0]["message"].get("content") or "").strip()
    citations = _extract_citations(data)
    return {"text": text, "citations": citations,
            "model": data.get("model", model), "raw": data}
