"""
validate_company.py — LangGraph node: Perplexity-Call 2, validiert Brand-Liste
und sammelt Sales-relevante Firmenfakten (Umsatz, MA, HQ, Modell).

Reads:  state["current_lead"]["Firma"], state["discovered_brands"]
Writes: state["validated_brands"], state["company_revenue"],
        state["company_employees"], state["company_hq"],
        state["primary_markets"], state["business_model"],
        state["sales_signals"], state["_employee_count_estimate"]
"""
from __future__ import annotations

import logging

from app.agents.lead_qualifying.services.perplexity_websearch import validate_company_sales
from app.agents.lead_qualifying.state import LeadState

logger = logging.getLogger(__name__)


async def validate_company_node(state: LeadState) -> LeadState:
    lead   = state.get("current_lead", {})
    firma  = str(lead.get("Firma", "")).strip()
    brands = state.get("discovered_brands") or []

    if not firma:
        return {
            **state,
            "validated_brands": [],
            "company_revenue":   "",
            "company_employees": "",
            "company_hq":        "",
            "primary_markets":   [],
            "business_model":    "",
            "sales_signals":     "",
            "_employee_count_estimate": "",
        }

    logger.info("validate_company: '%s' (Input: %d Brands)", firma, len(brands))
    data = await validate_company_sales(firma, brands)

    validated  = data.get("validated_brands") or []
    employees  = str(data.get("employee_count", "") or "").strip()
    revenue    = str(data.get("revenue_estimate", "") or "").strip()
    hq         = str(data.get("headquarters", "") or "").strip()
    markets    = data.get("primary_markets") or []
    biz_model  = str(data.get("business_model", "") or "").strip()
    signals    = str(data.get("sales_signals", "") or "").strip()

    logger.info(
        "validate_company: '%s' → %d validierte Brands, revenue=%s, employees=%s, model=%s",
        firma, len(validated), revenue, employees, biz_model,
    )

    return {
        **state,
        "validated_brands":         validated,
        "company_revenue":          revenue,
        "company_employees":        employees,
        "company_hq":               hq,
        "primary_markets":          markets if isinstance(markets, list) else [],
        "business_model":           biz_model,
        "sales_signals":            signals,
        # Mit der alten enrich_company-Pipeline kompatibel:
        "_employee_count_estimate": employees,
        "company_website":          (validated[0].get("domain") if validated else "") or state.get("company_website", ""),
    }
