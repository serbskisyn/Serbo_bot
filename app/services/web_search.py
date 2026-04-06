import logging
from tavily import AsyncTavilyClient
from app import config

logger = logging.getLogger(__name__)


async def search(query: str, max_results: int = 5) -> list[dict]:
    """
    Tavily Search — speziell für LLM-Agenten optimiert.
    Gibt Liste von {title, url, snippet} zurück.
    """
    try:
        client = AsyncTavilyClient(api_key=config.TAVILY_API_KEY)
        response = await client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
        )
        results = []
        for r in response.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            })
        logger.info(f"Tavily Search: '{query}' → {len(results)} Ergebnisse")
        return results

    except Exception as e:
        logger.error(f"Tavily Search Fehler: {e}")
        return []


def format_results(results: list[dict]) -> str:
    """Formatiert Suchergebnisse als Kontext für den LLM."""
    if not results:
        return "Keine Suchergebnisse gefunden."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}\n{r['snippet']}\nQuelle: {r['url']}")
    return "\n\n".join(lines)
