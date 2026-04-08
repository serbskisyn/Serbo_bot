import logging
import httpx
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

SUMMARIZE_PROMPT = """Du bist ein Sportjournalist. Du bekommst Titel und Beschreibung eines Fußball-Artikels über {club}.
Erstelle daraus:
1. Eine schlagkräftige deutsche Überschrift (max 10 Wörter, kein Clickbait)
2. Ein prägnantes Snippet auf Deutsch (max 50 Wörter) mit den wichtigsten Fakten

Antworte NUR in diesem Format:
HEADLINE: <überschrift>
SNIPPET: <snippet>

Wenn der Artikel nichts mit {club} zu tun hat, antworte exakt mit:
IRRELEVANT"""


async def _resolve_url(url: str) -> str:
    """
    Löst Redirects auf via GET mit kurzem Timeout.
    Gibt finale URL zurück oder originale URL bei Fehler.
    """
    try:
        async with httpx.AsyncClient(
            timeout=6.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36"
            }
        ) as client:
            r = await client.get(url)
            final = str(r.url)
            # Wenn finale URL nur Startseite ist (kein Pfad) → originale behalten
            path = final.rstrip("/").split("/", 3)
            if len(path) < 4 or not path[3]:
                logger.debug(f"Redirect zu Startseite ignoriert: {final}")
                return url
            return final
    except Exception as e:
        logger.debug(f"URL-Auflösung fehlgeschlagen ({url[:60]}): {e}")
        return url


async def _llm_summarize(title: str, snippet: str, fallback_title: str, club: str) -> tuple[str, str] | None:
    try:
        prompt   = SUMMARIZE_PROMPT.replace("{club}", club)
        content  = f"Titel: {title}\n\nBeschreibung: {snippet}"
        response = await ask_llm(content, history=[], system_prompt=prompt)

        if response.strip().upper() == "IRRELEVANT":
            return None

        headline    = fallback_title
        out_snippet = ""
        for line in response.splitlines():
            if line.startswith("HEADLINE:"):
                headline    = line.replace("HEADLINE:", "").strip()
            elif line.startswith("SNIPPET:"):
                out_snippet = line.replace("SNIPPET:", "").strip()
        return headline, out_snippet
    except Exception as e:
        logger.warning(f"LLM Summarize fehlgeschlagen: {e}")
        return fallback_title, snippet


async def enrich_news_item(url: str, title: str, snippet: str, club: str) -> dict | None:
    real_url = await _resolve_url(url)
    result   = await _llm_summarize(title, snippet, title, club)

    if result is None:
        return None

    headline, out_snippet = result
    return {
        "url":      real_url,
        "headline": headline,
        "snippet":  out_snippet,
    }