"""
enrich_company.py — LangGraph node: enrich company information via Perplexity web search.

Nutzt Perplexity Sonar Pro (OpenRouter) — Live-Web-Suche eingebaut, eine LLM-Call.
Fallback auf Gemini falls Perplexity scheitert (Quota, Timeout, Konfig-Fehler).
Northdata wird parallel als Stub geprüft.
"""
from __future__ import annotations

import logging

from app.agents.lead_qualifying.services.gemini_websearch import (
    enrich_company as gemini_enrich_company,
    get_news_summary as gemini_news_summary,
)
from app.agents.lead_qualifying.services.perplexity_websearch import (
    enrich_company as perplexity_enrich_company,
    get_news_summary as perplexity_news_summary,
)
from app.agents.lead_qualifying.services.northdata_lookup import get_company_summary
from app.agents.lead_qualifying.state import LeadState

logger = logging.getLogger(__name__)


def _has_useful_data(data: dict) -> bool:
    """Heuristik: gibt's Substanz im Perplexity-Ergebnis, oder soll wir Gemini probieren?"""
    return bool(
        data.get("company_website")
        or data.get("company_description")
        or data.get("industry")
        or data.get("employee_count_estimate")
    )


async def enrich_company_node(state: LeadState) -> LeadState:
    """
    Firmen-Recherche der aktuellen Lead.

    Primär: Perplexity Sonar Pro (Live-Search). Fallback: Gemini 2.0 Flash + Google Search.
    Northdata parallel als Stub.

    Reads:  state["current_lead"]
    Writes: state["company_website"], state["northdata_summary"], state["news_summary"],
            sowie Zwischenfelder für qualify_business_fit_node.
    """
    lead  = state.get("current_lead", {})
    firma = str(lead.get("Firma", "")).strip()

    logger.info("enrich_company: '%s' (via Perplexity Sonar Pro)", firma)

    # 1. Perplexity primär, Gemini als Fallback bei leerem Ergebnis
    company_data = await perplexity_enrich_company(firma)
    if not _has_useful_data(company_data):
        logger.info("enrich_company: Perplexity-Ergebnis leer für '%s' — Fallback Gemini", firma)
        company_data = await gemini_enrich_company(firma)
    company_website = company_data.get("company_website", "")
    company_description = company_data.get("company_description", "")
    industry = company_data.get("industry", "")
    employee_count_estimate = company_data.get("employee_count_estimate", "")
    ecommerce_signals = company_data.get("ecommerce_signals", "")

    # Append ecommerce signals to description for qualifier context
    if ecommerce_signals and ecommerce_signals.lower() != "keine gefunden":
        company_description = f"{company_description} | E-Commerce-Signale: {ecommerce_signals}".strip(" |")

    # 2. News-Recherche: Perplexity primär, Gemini-Fallback bei leerem Ergebnis
    news_summary = ""
    try:
        news_summary = await perplexity_news_summary(firma)
        if not news_summary or news_summary.startswith("Keine aktuellen Nachrichten"):
            try:
                gemini_news = await gemini_news_summary(firma)
                if gemini_news and not gemini_news.startswith("Keine aktuellen Nachrichten"):
                    news_summary = gemini_news
            except Exception as exc:
                logger.debug("enrich_company: Gemini-News-Fallback Fehler für '%s': %s", firma, exc)
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
