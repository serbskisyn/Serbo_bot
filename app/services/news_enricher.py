import logging
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
    """
    Anreicherung eines News-Items.
    URL wird direkt übernommen — kein Redirect nötig da GNews echte URLs liefert.
    Gibt None zurück wenn nicht club-relevant.
    """
    result = await _llm_summarize(title, snippet, title, club)
    if result is None:
        return None

    headline, out_snippet = result
    return {
        "url":      url,   # originale GNews URL direkt
        "headline": headline,
        "snippet":  out_snippet,
    }