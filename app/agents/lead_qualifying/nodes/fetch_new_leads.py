"""
fetch_new_leads.py — LangGraph node: read Inbound tab and filter by lead key.

Reads all rows from the Inbound Google Sheet tab, computes a SHA-256
lead_key for each row, and removes any already present in the Qualified
Leads tab (idempotency guard).
"""
from __future__ import annotations

import hashlib
import logging
import os

from app.agents.lead_qualifying.services.sheets import read_inbound_leads
from app.agents.lead_qualifying.state import LeadState

logger = logging.getLogger(__name__)

# Maximale Leads pro Run — verhindert Runaway-Backfill-Kosten bei großem Backlog.
# 0 = unbegrenzt. Default 30 ≈ 10-15 Min pro Run (Pepper-Subprocess dominiert).
_MAX_LEADS_PER_RUN = int(os.getenv("LEAD_QUALIFYING_MAX_PER_RUN", "30"))


def _compute_lead_key(row: dict) -> str:
    """SHA-256 of 'Vorname|Nachname|Firma|E-Mail'."""
    raw = "|".join([
        str(row.get("Vorname", "")),
        str(row.get("Nachname", "")),
        str(row.get("Firma", "")),
        str(row.get("E-Mail", "")),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def fetch_new_leads_node(state: LeadState) -> LeadState:
    """
    Read the Inbound tab and filter leads not yet in Qualified Leads.

    Populates:
      state["raw_leads"]   — all rows from Inbound
      state["new_leads"]   — rows not yet processed (with _lead_key injected)
    """
    logger.info("fetch_new_leads: Lese Inbound-Tab …")

    try:
        raw_leads = await read_inbound_leads()
    except Exception as exc:
        logger.error("fetch_new_leads: Fehler beim Lesen des Inbound-Tabs: %s", exc)
        return {
            **state,
            "raw_leads": [],
            "new_leads": [],
            "errors": [*state.get("errors", []), f"Inbound-Tab nicht lesbar: {exc}"],
        }

    # Inject computed lead_key into each row (gespeichert für Qualified-Leads-Tab + Logs)
    for row in raw_leads:
        row["_lead_key"] = _compute_lead_key(row)

    # Single Source of Truth für Idempotenz: Validierung_Datum im Inbound-Tab.
    # Qualified-Leads-Tab bleibt als Audit-Log, ist aber nicht mehr Idempotenz-Quelle.
    new_leads = [r for r in raw_leads if not r.get("_validierung_datum")]
    skipped_validated = len(raw_leads) - len(new_leads)
    if skipped_validated:
        logger.info("fetch_new_leads: %d Leads bereits validiert (Datum gesetzt) — übersprungen", skipped_validated)

    # Skip rows that lack a name and a company (likely empty sheet rows)
    new_leads = [
        r for r in new_leads
        if (r.get("Vorname") or r.get("Nachname") or r.get("Firma"))
    ]

    total_candidates = len(new_leads)
    if _MAX_LEADS_PER_RUN > 0 and total_candidates > _MAX_LEADS_PER_RUN:
        # Älteste Leads zuerst (Sheet-Reihenfolge = Eingangsdatum)
        new_leads = new_leads[: _MAX_LEADS_PER_RUN]
        logger.warning(
            "fetch_new_leads: %d Leads im Backlog — verarbeite nur %d in diesem Run "
            "(LEAD_QUALIFYING_MAX_PER_RUN). Rest folgt im nächsten Slot.",
            total_candidates, _MAX_LEADS_PER_RUN,
        )

    logger.info(
        "fetch_new_leads: %d Inbound-Zeilen, %d bereits validiert, %d neu",
        len(raw_leads),
        skipped_validated,
        len(new_leads),
    )

    return {
        **state,
        "raw_leads": raw_leads,
        "new_leads": new_leads,
        "processed_leads": state.get("processed_leads", []),
        "errors": state.get("errors", []),
    }
