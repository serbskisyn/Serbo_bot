"""
enrich_commercial_intelligence.py — LangGraph node: performance marketing + affiliate signals.

Reads:  state["current_lead"]["Firma"], state["validated_brands"],
        state["business_model"], state["primary_markets"], state["company_revenue"]
Writes: state["marketing_spend_estimate"], state["perf_mktg_signals"],
        state["affiliate_likelihood"], state["promo_intensity"],
        state["commercial_intel_summary"]
"""
from __future__ import annotations

import logging

from app.agents.lead_qualifying.services.perplexity_websearch import enrich_commercial_intelligence
from app.agents.lead_qualifying.state import LeadState

logger = logging.getLogger(__name__)


async def enrich_commercial_intelligence_node(state: LeadState) -> LeadState:
    lead   = state.get("current_lead", {})
    firma  = str(lead.get("Firma", "")).strip()

    if not firma:
        return {
            **state,
            "marketing_spend_estimate": "",
            "perf_mktg_signals":        "",
            "affiliate_likelihood":     "",
            "promo_intensity":          "",
            "commercial_intel_summary": "",
        }

    brands   = state.get("validated_brands") or state.get("discovered_brands") or []
    biz_model = str(state.get("business_model", "") or "")
    markets   = list(state.get("primary_markets") or [])
    revenue   = str(state.get("company_revenue", "") or "unknown")

    logger.info("enrich_commercial_intelligence: '%s' (%d brands)", firma, len(brands))

    data = await enrich_commercial_intelligence(firma, brands, biz_model, markets, revenue)

    # ── Build compact signal strings for downstream nodes / sheet ─────────────

    # Performance marketing channels summary
    channels = []
    for field, label in [
        ("google_shopping_presence", "GShop"),
        ("meta_tiktok_activity",     "Meta/TikTok"),
        ("amazon_ads_presence",      "Amazon Ads"),
    ]:
        level = str(data.get(field, "none") or "none").lower()
        if level not in ("none", "low", "unknown", ""):
            channels.append(f"{label}:{level}")
    perf_signals = ", ".join(channels) if channels else "no strong signals"

    # Affiliate likelihood string
    affiliate_raw = str(data.get("affiliate_likelihood", "low") or "low").lower()
    networks = data.get("affiliate_networks") or []
    if networks:
        affiliate_str = f"{affiliate_raw} — {', '.join(networks[:4])}"
    else:
        affiliate_str = affiliate_raw

    # Promo intensity
    promo_raw = str(data.get("promo_intensity_summary", "") or "").strip()
    promo_freq = str(data.get("coupon_promo_frequency", "none") or "none").lower()
    if promo_raw:
        promo_str = promo_raw
    else:
        promo_str = promo_freq

    # Full commercial intelligence summary (for LLM context in qualify_business_fit)
    spend    = str(data.get("marketing_spend_estimate", "unknown") or "unknown")
    perf_soph = str(data.get("perf_mktg_sophistication", "low") or "low").lower()
    deal_comm = str(data.get("deal_community_presence", "none") or "none").lower()
    cashback  = data.get("cashback_platform_presence") or []
    bf_sig    = bool(data.get("bf_prime_day_signals", False))

    summary_parts = [
        f"Marketing spend: {spend}, sophistication: {perf_soph}.",
        f"Performance channels: {perf_signals or 'none detected'}.",
    ]
    if deal_comm != "none":
        summary_parts.append(f"Deal community presence: {deal_comm}.")
    if cashback:
        summary_parts.append(f"Cashback platforms: {', '.join(cashback[:4])}.")
    if bf_sig:
        summary_parts.append("BF/Peak-day promotions detected.")
    if promo_raw:
        summary_parts.append(promo_raw)

    commercial_summary = " ".join(summary_parts)

    logger.info(
        "enrich_commercial_intelligence: '%s' → affiliate=%s perf_mktg=%s deal_community=%s",
        firma, affiliate_raw, perf_soph, deal_comm,
    )

    return {
        **state,
        "marketing_spend_estimate": spend,
        "perf_mktg_signals":        perf_signals,
        "affiliate_likelihood":     affiliate_str,
        "promo_intensity":          promo_str,
        "commercial_intel_summary": commercial_summary,
    }
