import logging
import httpx
from app.config import TAVILY_API_KEY
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"

SUMMARIZE_PROMPT = """Du bist ein Sportjournalist. Du bekommst den Rohtext eines Fußball-Artikels über {club}.
Erstelle daraus:
1. Eine schlagkräftige deutsche Überschrift (max 10 Wörter, kein Clickbait)
2. Ein prägnantes Snippet auf Deutsch (max 50 Wörter) mit den wichtigsten Fakten

Antworte NUR in diesem Format:
HEADLINE: <überschrift>
SNIPPET: <snippet>

Wenn der Artikel nichts mit {club} zu tun hat, antworte exakt mit:
IRRELEVANT"""


async def _resolve_redirect(url: str) -> str:
    """Löst Google-News-Redirects zur echten Artikel-URL auf via GET."""
    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"}
        ) as client:
            r = await client.get(url)
            return str(r.url)
    except Exception as e:
        logger.warning(f"Redirect-Auflösung fehlgeschlagen ({url[:60]}): {e}")
        return url


async def _tavily_extract(url: str) -> str | None:
    """Extrahiert den Artikel-Text via Tavily Extract API."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                TAVILY_EXTRACT_URL,
                json={"urls": [url], "api_key": TAVILY_API_KEY},
            )
            r.raise_for_status()
            data    = r.json()
            results = data.get("results", [])
            if not results:
                return None
            raw = results[0].get("raw_content", "")
            return raw[:2000].strip() if raw else None
    except Exception as e:
        logger.warning(f"Tavily Extract fehlgeschlagen ({url[:60]}): {e}")
        return None


async def _llm_summarize(raw_text: str, fallback_title: str, club: str) -> tuple[str, str] | None:
    """
    Generiert deutsche Headline + Snippet via LLM.
    Gibt None zurück wenn Artikel nicht club-relevant ist.
    """
    try:
        prompt   = SUMMARIZE_PROMPT.replace("{club}", club)
        response = await ask_llm(
            f"Artikel-Text:\n\n{raw_text}",
            history=[],
            system_prompt=prompt,
        )
        if response.strip().upper() == "IRRELEVANT":
            return None

        headline = fallback_title
        snippet  = ""
        for line in response.splitlines():
            if line.startswith("HEADLINE:"):
                headline = line.replace("HEADLINE:", "").strip()
            elif line.startswith("SNIPPET:"):
                snippet = line.replace("SNIPPET:", "").strip()
        return headline, snippet
    except Exception as e:
        logger.warning(f"LLM Summarize fehlgeschlagen: {e}")
        return fallback_title, ""


async def enrich_news_item(url: str, fallback_title: str, club: str) -> dict | None:
    """
    Vollständige Anreicherung eines News-Items.
    Gibt None zurück wenn Artikel nicht club-relevant ist.
    """
    real_url = await _resolve_redirect(url)
    raw_text = await _tavily_extract(real_url)

    if raw_text:
        result = await _llm_summarize(raw_text, fallback_title, club)
        if result is None:
            logger.info(f"Artikel als irrelevant markiert: {real_url[:60]}")
            return None
        headline, snippet = result
    else:
        headline = fallback_title
        snippet  = ""

    return {
        "url":      real_url,
        "headline": headline,
        "snippet":  snippet,
    }