"""
enrich_company.py — LangGraph node: enrich company information via Gemini web search.

Uses a single Gemini 2.0 Flash call (with Google Search grounding) to research the
company — no separate SerpAPI step required. Northdata is queried in parallel as a stub.
"""
from __future__ import annotations

import logging

from app.agents.lead_qualifying.services.gemini_websearch import (
    enrich_company as gemini_enrich_company,
    get_news_summary as gemini_news_summary,
)
from app.agents.lead_qualifying.services.northdata_lookup import get_company_summary
from app.agents.lead_qualifying.state import LeadState

logger = logging.getLogger(__name__)


async def enrich_company_node(state: LeadState) -> LeadState:
    """
    Enrich the current lead's company information.

    Uses Gemini 2.0 Flash + Google Search grounding for a single-call enrichment.
    Northdata is fetched in parallel (stub until API key is available).

    Reads:  state["current_lead"]
    Writes: state["company_website"], state["northdata_summary"],
            state["news_summary"], and intermediate fields for qualify node.
    """
    lead = state.get("current_lead", {})
    firma = str(lead.get("Firma", "")).strip()

    logger.info("enrich_company: '%s' (via Gemini web search)", firma)

    # 1. Gemini: company research (searches + synthesises in one call)
    company_data = await gemini_enrich_company(firma)
    company_website = company_data.get("company_website", "")
    company_description = company_data.get("company_description", "")
    industry = company_data.get("industry", "")
    employee_count_estimate = company_data.get("employee_count_estimate", "")
    ecommerce_signals = company_data.get("ecommerce_signals", "")

    # Append ecommerce signals to description for qualifier context
    if ecommerce_signals and ecommerce_signals.lower() != "keine gefunden":
        company_description = f"{company_description} | E-Commerce-Signale: {ecommerce_signals}".strip(" |")

    # 2. Gemini: recent news (separate focused call for news signals)
    news_summary = ""
    try:
        news_summary = await gemini_news_summary(firma)
    except Exception as exc:
        logger.warning("enrich_company: News-Lookup Fehler für '%s': %s", firma, exc)

    # 3. Northdata (stub — logs warning when NORTHDATA_API_KEY missing)
    northdata_summary = ""
    try:
        northdata_summary = await get_company_summary(firma)
    except Exception as exc:
        logger.warning("enrich_company: Northdata Fehler für '%s': %s", firma, exc)

    logger.info(
        "enrich_company: website='%s' industry='%s' size='%s'",
        company_website, industry, employee_count_estimate,
    )

    return {
        **state,
        "company_website": company_website,
        "northdata_summary": northdata_summary,
        "news_summary": news_summary,
        # Intermediate values consumed by qualify_business_fit_node
        "_company_description": company_description,
        "_industry": industry,
        "_employee_count_estimate": employee_count_estimate,
    }
