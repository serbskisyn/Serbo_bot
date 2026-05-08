import httpx
import logging
from tavily import AsyncTavilyClient
from app import config

logger = logging.getLogger(__name__)


async def _search_brave(query: str, max_results: int) -> list[dict]:
    if not config.BRAVE_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": config.BRAVE_API_KEY,
                },
            )
            r.raise_for_status()
            results = []
            for item in r.json().get("web", {}).get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("description", ""),
                })
            logger.info("Brave Search: '%s' → %d Ergebnisse", query, len(results))
            return results
    except Exception as e:
        logger.warning("Brave Search Fehler: %s", e)
        return []


async def search(query: str, max_results: int = 5) -> list[dict]:
    """Tavily primär, Brave als Fallback wenn Tavily leer oder Fehler."""
    try:
        client = AsyncTavilyClient(api_key=config.TAVILY_API_KEY)
        response = await client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
        )
        results = [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
            for r in response.get("results", [])
        ]
        if results:
            logger.info("Tavily Search: '%s' → %d Ergebnisse", query, len(results))
            return results
        logger.warning("Tavily lieferte keine Ergebnisse, versuche Brave")
    except Exception as e:
        logger.warning("Tavily Search Fehler: %s — versuche Brave", e)

    return await _search_brave(query, max_results)


def format_results(results: list[dict]) -> str:
    """Formatiert Suchergebnisse als Kontext für den LLM."""
    if not results:
        return "Keine Suchergebnisse gefunden."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}\n{r['snippet']}\nQuelle: {r['url']}")
    return "\n\n".join(lines)
