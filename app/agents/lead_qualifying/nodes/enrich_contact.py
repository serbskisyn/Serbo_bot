"""
enrich_contact.py — LangGraph node: enrich contact information via Gemini web search.

Uses a single Gemini 2.0 Flash call (with Google Search grounding) to find the
contact's job title and LinkedIn URL — no SerpAPI step required.
Proxycurl can be wired in later as a drop-in replacement for higher data quality.
"""
from __future__ import annotations

import logging

from app.agents.lead_qualifying.services.gemini_websearch import enrich_contact as gemini_enrich_contact
from app.agents.lead_qualifying.state import LeadState

logger = logging.getLogger(__name__)


async def enrich_contact_node(state: LeadState) -> LeadState:
    """
    Enrich the current lead's contact information.

    Uses Gemini 2.0 Flash + Google Search grounding for a single-call lookup.
    TODO: swap gemini_enrich_contact() for Proxycurl when API key is available.

    Reads:  state["current_lead"]
    Writes: state["contact_title"], state["linkedin_url"]
    """
    lead = state.get("current_lead", {})
    vorname = str(lead.get("Vorname", "")).strip()
    nachname = str(lead.get("Nachname", "")).strip()
    firma = str(lead.get("Firma", "")).strip()
    name = f"{vorname} {nachname}".strip()

    logger.info("enrich_contact: '%s' @ '%s' (via Gemini web search)", name, firma)

    contact_title = ""
    linkedin_url = ""

    try:
        data = await gemini_enrich_contact(vorname, nachname, firma)
        contact_title = data.get("contact_title", "")
        linkedin_url = data.get("linkedin_url", "")
    except Exception as exc:
        logger.warning("enrich_contact: Fehler für '%s': %s", name, exc)

    logger.info(
        "enrich_contact: title='%s' linkedin='%s'",
        contact_title, linkedin_url or "(nicht gefunden)",
    )

    return {
        **state,
        "contact_title": contact_title,
        "linkedin_url": linkedin_url,
    }
