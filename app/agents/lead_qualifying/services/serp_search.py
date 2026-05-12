"""
serp_search.py — SerpAPI / Google Search wrapper for lead enrichment.

Uses SerpAPI when SERP_API_KEY is set, otherwise falls back to the existing
web_search service (Tavily → Brave).
"""
from __future__ import annotations

import logging

import httpx

from app import config

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search"


async def _search_serpapi(query: str, num: int = 5) -> list[dict]:
    """Query SerpAPI Google search and return normalised result dicts."""
    api_key = config.SERP_API_KEY or None
    if not api_key:
        logger.debug("SERP_API_KEY not set, skipping SerpAPI")
        return []

    params = {
        "engine": "google",
        "q": query,
        "num": num,
        "hl": "de",
        "gl": "de",
        "api_key": api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(SERPAPI_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("organic_results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })

        logger.info("SerpAPI '%s' → %d Ergebnisse", query, len(results))
        return results

    except httpx.TimeoutException:
        logger.warning("SerpAPI Timeout für Query: %s", query)
        return []
    except httpx.HTTPStatusError as exc:
        logger.warning("SerpAPI HTTP Fehler %s für Query: %s", exc.response.status_code, query)
        return []
    except Exception as exc:
        logger.warning("SerpAPI unbekannter Fehler: %s", exc)
        return []


async def search(query: str, num: int = 5) -> list[dict]:
    """
    Primary search entry-point.

    Tries SerpAPI first; falls back to the existing web_search service
    (Tavily → Brave) when SerpAPI is unavailable or returns nothing.
    """
    results = await _search_serpapi(query, num=num)
    if results:
        return results

    # Fallback to existing search infrastructure
    try:
        from app.services.web_search import search as fallback_search
        results = await fallback_search(query, max_results=num)
        if results:
            logger.info("Fallback-Search '%s' → %d Ergebnisse", query, len(results))
        return results
    except Exception as exc:
        logger.warning("Fallback-Search Fehler: %s", exc)
        return []


def format_for_prompt(results: list[dict]) -> str:
    """Format search results as a compact string for LLM prompts."""
    if not results:
        return "Keine Suchergebnisse gefunden."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.get('title', '')}\n{r.get('snippet', '')}\nURL: {r.get('url', '')}")
    return "\n\n".join(lines)
