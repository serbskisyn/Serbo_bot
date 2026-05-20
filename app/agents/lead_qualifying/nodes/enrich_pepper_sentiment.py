"""
enrich_pepper_sentiment.py — LangGraph node: lookup brand sentiment in Pepper.

Ruft pepper_lookup.get_brand_sentiment() via Claude Code Subprocess auf (Pfad C —
nutzt die Atolls Claude-Teams-MCP-Verbindung). Bei jedem Fehler bleibt found=False,
damit die Pipeline weiterläuft.

Reads:  state["current_lead"]["Firma"]
Writes: state["pepper_*"] + state["pepper_summary"]
"""
from __future__ import annotations

import logging

from app.agents.lead_qualifying.services.pepper_lookup import (
    format_sentiment_summary,
    get_brand_sentiment,
)
from app.agents.lead_qualifying.state import LeadState

logger = logging.getLogger(__name__)


async def enrich_pepper_sentiment_node(state: LeadState) -> LeadState:
    lead  = state.get("current_lead", {})
    firma = str(lead.get("Firma", "")).strip()

    if not firma:
        logger.info("enrich_pepper_sentiment: Lead ohne Firma — skip")
        return {
            **state,
            "pepper_found": False,
            "pepper_summary": "Keine Firma",
        }

    logger.info("enrich_pepper_sentiment: lookup für '%s'", firma)
    result = await get_brand_sentiment(firma)

    summary = format_sentiment_summary(result)
    pos_rate = result.get("pos_rate")
    # TypedDict expects float — bei None speichern wir -1.0 als Sentinel
    pos_rate_float = float(pos_rate) if pos_rate is not None else -1.0

    return {
        **state,
        "pepper_found":          bool(result.get("found", False)),
        "pepper_matched_name":   result.get("matched_name") or "",
        "pepper_total_mentions": int(result.get("total_mentions") or 0),
        "pepper_pos":            int(result.get("pos") or 0),
        "pepper_neg":            int(result.get("neg") or 0),
        "pepper_neu":            int(result.get("neu") or 0),
        "pepper_pos_rate":       pos_rate_float,
        "pepper_top_country":    (result.get("top_country") or "").upper(),
        "pepper_summary":        summary,
    }
