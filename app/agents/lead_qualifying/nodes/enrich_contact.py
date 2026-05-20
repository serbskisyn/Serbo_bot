"""
enrich_contact.py — LangGraph node: enrich contact information via Perplexity (Gemini-Fallback).

Primär: Perplexity Sonar Pro mit Live-Web-Suche. Fallback auf Gemini bei leerem Ergebnis.
Proxycurl wäre der nächste Schritt für höhere Datenqualität.
"""
from __future__ import annotations

import logging

from app.agents.lead_qualifying.services.gemini_websearch import enrich_contact as gemini_enrich_contact
from app.agents.lead_qualifying.services.perplexity_websearch import enrich_contact as perplexity_enrich_contact
from app.agents.lead_qualifying.state import LeadState

logger = logging.getLogger(__name__)


async def enrich_contact_node(state: LeadState) -> LeadState:
    """
    Enrich the current lead's contact information.

    Primär: Perplexity Sonar Pro (Live-Suche). Fallback: Gemini wenn Perplexity leer.

    Reads:  state["current_lead"]
    Writes: state["contact_title"], state["linkedin_url"]
    """
    lead     = state.get("current_lead", {})
    vorname  = str(lead.get("Vorname", "")).strip()
    nachname = str(lead.get("Nachname", "")).strip()
    firma    = str(lead.get("Firma", "")).strip()
    name     = f"{vorname} {nachname}".strip()

    logger.info("enrich_contact: '%s' @ '%s' (via Perplexity)", name, firma)

    contact_title = ""
    linkedin_url  = ""

    try:
        data = await perplexity_enrich_contact(vorname, nachname, firma)
        contact_title = data.get("contact_title", "") or ""
        linkedin_url  = data.get("linkedin_url", "") or ""

        # Fallback: wenn Perplexity nichts geliefert hat, Gemini versuchen
        if not contact_title and not linkedin_url:
            logger.info("enrich_contact: Perplexity leer für '%s' — Fallback Gemini", name)
            data = await gemini_enrich_contact(vorname, nachname, firma)
            contact_title = data.get("contact_title", "") or ""
            linkedin_url  = data.get("linkedin_url", "") or ""
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
