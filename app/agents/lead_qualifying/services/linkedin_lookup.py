"""
linkedin_lookup.py — Indirect LinkedIn profile lookup via public search results.

Does NOT scrape LinkedIn directly (ToS violation). Instead uses SerpAPI /
search to find public LinkedIn profile URLs from Google search results.
"""
from __future__ import annotations

import logging
import re

from app.agents.lead_qualifying.services import serp_search

logger = logging.getLogger(__name__)

_LINKEDIN_PATTERN = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[^\s\"'>]+", re.IGNORECASE)
_LINKEDIN_COMPANY_PATTERN = re.compile(r"https?://(?:www\.)?linkedin\.com/company/[^\s\"'>]+", re.IGNORECASE)


async def find_profile_url(vorname: str, nachname: str, firma: str) -> str:
    """
    Search for a LinkedIn profile URL for the given contact.

    Returns the best match URL string or an empty string if not found.
    No direct LinkedIn scraping — only surface-level URL extraction from
    Google search snippets.
    """
    name = f"{vorname} {nachname}".strip()
    query = f'site:linkedin.com/in "{name}" "{firma}"'
    results = await serp_search.search(query, num=3)

    for r in results:
        # Try the URL itself first (SerpAPI often returns the direct profile link)
        url = r.get("url", "")
        if _LINKEDIN_PATTERN.match(url):
            logger.info("LinkedIn-Profil gefunden für %s: %s", name, url)
            return url

        # Try snippet text as fallback
        snippet = r.get("snippet", "") + " " + r.get("title", "")
        match = _LINKEDIN_PATTERN.search(snippet)
        if match:
            logger.info("LinkedIn-Profil aus Snippet für %s: %s", name, match.group())
            return match.group()

    # Looser query without quotes
    query_loose = f"LinkedIn {name} {firma}"
    results_loose = await serp_search.search(query_loose, num=3)
    for r in results_loose:
        url = r.get("url", "")
        if _LINKEDIN_PATTERN.match(url):
            logger.info("LinkedIn-Profil (lose Suche) für %s: %s", name, url)
            return url

    logger.info("Kein LinkedIn-Profil gefunden für %s (%s)", name, firma)
    return ""
