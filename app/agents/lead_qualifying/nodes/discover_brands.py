"""
discover_brands.py — LangGraph node: Perplexity-Call 1, findet eCommerce-Marken
des Lead-Unternehmens.

Reads:  state["current_lead"]["Firma"]
Writes: state["discovered_brands"], state["is_holding"]
"""
from __future__ import annotations

import logging

from app.agents.lead_qualifying.services.perplexity_websearch import discover_ecommerce_brands
from app.agents.lead_qualifying.state import LeadState

logger = logging.getLogger(__name__)


async def discover_brands_node(state: LeadState) -> LeadState:
    lead  = state.get("current_lead", {})
    firma = str(lead.get("Firma", "")).strip()

    if not firma:
        logger.info("discover_brands: Lead ohne Firma — skip")
        return {**state, "discovered_brands": [], "is_holding": False}

    logger.info("discover_brands: '%s'", firma)
    data = await discover_ecommerce_brands(firma)
    brands = data.get("brands") or []
    logger.info("discover_brands: '%s' → %d Marken (holding=%s, confidence=%s)",
                firma, len(brands), data.get("is_holding"), data.get("confidence"))
    return {
        **state,
        "discovered_brands": brands,
        "is_holding": bool(data.get("is_holding", False)),
    }
