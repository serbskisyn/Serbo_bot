"""
news_lookup.py — Recent news lookup for a company.

Delegates to gemini_websearch.get_news_summary() which uses Gemini 2.0 Flash
with Google Search grounding for a single-call search + synthesis.
"""
from __future__ import annotations

import logging

from app.agents.lead_qualifying.services.gemini_websearch import get_news_summary as _gemini_news

logger = logging.getLogger(__name__)


async def get_news_summary(firma: str) -> str:
    """
    Return a 2-3 sentence German news summary for a company.

    Backed by Gemini 2.0 Flash + Google Search grounding via OpenRouter.
    """
    if not firma.strip():
        return ""
    return await _gemini_news(firma)
