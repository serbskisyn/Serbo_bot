"""
enrich_contact_v2.py — LangGraph node: contact validation via Perplexity.

Identifies: job title, LinkedIn URL, decision authority (decision_maker /
influencer / other) and role relevance for Atolls platforms (marketing /
sales / eCommerce vs. tech/ops).

Reads:  state["current_lead"]
Writes: state["contact_title"], state["linkedin_url"],
        state["contact_authority"], state["contact_role_match"]
"""
from __future__ import annotations

import logging

from app.agents.lead_qualifying.services.perplexity_websearch import enrich_contact as perplexity_enrich_contact
from app.agents.lead_qualifying.state import LeadState

logger = logging.getLogger(__name__)


async def enrich_contact_v2_node(state: LeadState) -> LeadState:
    lead     = state.get("current_lead", {})
    vorname  = str(lead.get("Vorname", "")).strip()
    nachname = str(lead.get("Nachname", "")).strip()
    firma    = str(lead.get("Firma", "")).strip()
    name     = f"{vorname} {nachname}".strip()

    if not name or not firma:
        return {
            **state,
            "contact_title":      "",
            "linkedin_url":       "",
            "contact_authority":  "other",
            "contact_role_match": False,
        }

    logger.info("enrich_contact_v2: '%s' @ '%s'", name, firma)
    data = await perplexity_enrich_contact(vorname, nachname, firma)

    title       = (data.get("contact_title") or "").strip()
    linkedin    = (data.get("linkedin_url") or "").strip()
    authority   = (data.get("authority") or "other").strip().lower()
    role_match  = bool(data.get("role_match", False))

    if authority not in ("decision_maker", "influencer", "other"):
        authority = "other"

    logger.info(
        "enrich_contact_v2: title='%s' auth=%s role_match=%s linkedin=%s",
        title, authority, role_match, "yes" if linkedin else "no",
    )

    return {
        **state,
        "contact_title":      title,
        "linkedin_url":       linkedin,
        "contact_authority":  authority,
        "contact_role_match": role_match,
    }
