"""
graph.py — LangGraph StateGraph for the Lead Qualifying Agent.

Pipeline overview (per-lead loop handled in run_pipeline()):

  fetch_new_leads
      │
      └─ [for each lead]:
              pre_qualify
                  │
                  ├─ SKIP ──────────► collect_filtered_result → END
                  │
                  └─ HIGH / LOW ───► discover_brands
                                          │
                                     validate_company
                                          │
                                     enrich_commercial_intelligence
                                          │
                                     enrich_contact_v2
                                          │
                                     [Hard-Skip Check]
                                     ├─ B2B / no eCommerce signal ─► skip_pepper
                                     │                                    │
                                     └─ eCommerce relevant ─────────► pepper_multi_country
                                                                          │
                                                                     qualify_business_fit
                                                                          │
                                                                     collect_result → END
      │
  write_results  (flush all to sheet + Telegram, FILTERED excluded from Telegram)
"""
from __future__ import annotations

import logging

from langgraph.graph import StateGraph, END

from app.agents.lead_qualifying.state import LeadState
from app.agents.lead_qualifying.nodes.pre_qualify import (
    pre_qualify_node,
    route_after_pre_qualify,
)
from app.agents.lead_qualifying.nodes.discover_brands import discover_brands_node
from app.agents.lead_qualifying.nodes.validate_company import validate_company_node
from app.agents.lead_qualifying.nodes.enrich_commercial_intelligence import enrich_commercial_intelligence_node
from app.agents.lead_qualifying.nodes.enrich_contact_v2 import enrich_contact_v2_node
from app.agents.lead_qualifying.nodes.pepper_multi_country import pepper_multi_country_node
from app.agents.lead_qualifying.nodes.qualify_business_fit import qualify_business_fit_node
from app.agents.lead_qualifying.nodes.write_results import (
    collect_filtered_result_node,
    collect_lead_result_node,
    write_results_node,
)
from app.agents.lead_qualifying.nodes.fetch_new_leads import fetch_new_leads_node

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pepper hard-skip logic
# ---------------------------------------------------------------------------

def route_after_contact(state: LeadState) -> str:
    """
    Routing function called after enrich_contact_v2.

    Returns "skip_pepper" when Pepper would yield no signal:
      - business_model is 'B2B' (pure B2B, no consumer deal-platform presence)
      - OR all three weak signals are absent:
          validated_brands is empty AND contact_role_match is False
          AND contact_authority == "other"

    Otherwise returns "pepper_multi_country" to run the full Pepper lookup.
    """
    business_model = (state.get("business_model") or "").strip().upper()
    validated_brands = state.get("validated_brands") or []
    contact_role_match = state.get("contact_role_match", False)
    contact_authority = (state.get("contact_authority") or "other").lower()

    if business_model == "B2B":
        logger.info(
            "route_after_contact: '%s' — Pepper übersprungen (B2B)",
            state.get("current_lead", {}).get("Firma", "?"),
        )
        return "skip_pepper"

    if not validated_brands and not contact_role_match and contact_authority == "other":
        logger.info(
            "route_after_contact: '%s' — Pepper übersprungen (no eCommerce signal)",
            state.get("current_lead", {}).get("Firma", "?"),
        )
        return "skip_pepper"

    return "pepper_multi_country"


async def skip_pepper_node(state: LeadState) -> LeadState:
    """
    Tiny node that populates pepper fields with skip-sentinel values and
    routes directly to qualify_business_fit (bypassing the Pepper MCP call).
    """
    business_model = (state.get("business_model") or "").strip().upper()
    reason = "B2B" if business_model == "B2B" else "no eCommerce signal"
    logger.info(
        "skip_pepper: '%s' — Pepper übersprungen (%s)",
        state.get("current_lead", {}).get("Firma", "?"),
        reason,
    )
    return {
        **state,
        "pepper_by_brand": {},
        "pepper_brands_found": 0,
        "pepper_total_mentions_all": 0,
        "pepper_target_summary": "—",
        "pepper_cross_summary": "—",
        "pepper_summary": f"Skipped ({reason})",
    }


def build_per_lead_graph():
    """
    Build the sub-graph that processes a single lead end-to-end.

    Entry:  state must have "current_lead" set.
    Exit:   lead dict appended to state["processed_leads"]
            (classification = FILTERED for skipped leads, HOT/WARM/COLD otherwise).
    """
    graph = StateGraph(LeadState)

    graph.add_node("pre_qualify", pre_qualify_node)
    graph.add_node("discover_brands", discover_brands_node)
    graph.add_node("validate_company", validate_company_node)
    graph.add_node("enrich_commercial_intelligence", enrich_commercial_intelligence_node)
    graph.add_node("enrich_contact_v2", enrich_contact_v2_node)
    graph.add_node("skip_pepper", skip_pepper_node)
    graph.add_node("pepper_multi_country", pepper_multi_country_node)
    graph.add_node("qualify_business_fit", qualify_business_fit_node)
    graph.add_node("collect_result", collect_lead_result_node)
    graph.add_node("collect_filtered_result", collect_filtered_result_node)

    graph.set_entry_point("pre_qualify")

    # Conditional branch after pre-qualification (Fake-Lead-Filter)
    graph.add_conditional_edges(
        "pre_qualify",
        route_after_pre_qualify,
        {
            "enrich_contact": "discover_brands",        # routing-key for compat
            "collect_filtered_result": "collect_filtered_result",
        },
    )

    # Enrichment pipeline:
    # discover_brands → validate_company → enrich_contact_v2
    #   → [Hard-Skip Check] → skip_pepper OR pepper_multi_country
    #   → qualify_business_fit
    graph.add_edge("discover_brands", "validate_company")
    graph.add_edge("validate_company", "enrich_commercial_intelligence")
    graph.add_edge("enrich_commercial_intelligence", "enrich_contact_v2")
    graph.add_conditional_edges(
        "enrich_contact_v2",
        route_after_contact,
        {
            "pepper_multi_country": "pepper_multi_country",
            "skip_pepper": "skip_pepper",
        },
    )
    graph.add_edge("skip_pepper", "qualify_business_fit")
    graph.add_edge("pepper_multi_country", "qualify_business_fit")
    graph.add_edge("qualify_business_fit", "collect_result")
    graph.add_edge("collect_result", END)

    # Skip path
    graph.add_edge("collect_filtered_result", END)

    return graph.compile()


# Compiled sub-graph (module-level singleton, reused across leads)
_per_lead_graph = build_per_lead_graph()


async def run_pipeline(max_leads: int | None = None) -> LeadState:
    """
    Run the full lead qualifying pipeline.

    1. Fetch new leads from the Inbound tab.
    2. Pre-qualify each lead (raw data only, fast + cheap).
       - SKIP → mark as FILTERED, write to sheet, no Telegram
       - HIGH / LOW → full enrichment + qualification
    3. Flush all results to Google Sheets and send a Telegram summary.

    Args:
        max_leads: If provided, overrides the LEAD_QUALIFYING_MAX_PER_RUN env-var
                   limit. Useful for /leads N to process exactly N leads.

    Returns the final LeadState (useful for logging / testing).
    """
    initial_state: LeadState = {
        "raw_leads": [],
        "new_leads": [],
        "processed_leads": [],
        "errors": [],
    }
    if max_leads is not None:
        initial_state["max_leads_override"] = max_leads

    # ── Step 1: Fetch ────────────────────────────────────────────────────────
    state = await fetch_new_leads_node(initial_state)
    new_leads = state.get("new_leads", [])

    if not new_leads:
        logger.info("run_pipeline: Keine neuen Leads — Pipeline beendet")
        return state

    logger.info("run_pipeline: %d neue Leads werden verarbeitet", len(new_leads))

    # ── Step 2: Process each lead ────────────────────────────────────────────
    for i, lead in enumerate(new_leads, 1):
        firma = lead.get("Firma", "")
        name = f"{lead.get('Vorname', '')} {lead.get('Nachname', '')}".strip()
        logger.info("run_pipeline: Lead %d/%d — '%s' @ '%s'", i, len(new_leads), name, firma)

        lead_state: LeadState = {
            **state,
            "current_lead": lead,
            # Reset per-lead fields
            "pre_qualify_label": "",
            "pre_qualify_reason": "",
            "contact_title": "",
            "linkedin_url": "",
            "company_website": "",
            "northdata_summary": "",
            "news_summary": "",
            # Neue Pipeline-Felder
            "discovered_brands": [],
            "is_holding": False,
            "validated_brands": [],
            "company_revenue": "",
            "company_employees": "",
            "company_hq": "",
            "primary_markets": [],
            "business_model": "",
            "sales_signals": "",
            "target_country_iso": "",
            "pepper_by_brand": {},
            "pepper_brands_found": 0,
            "pepper_total_mentions_all": 0,
            "pepper_target_summary": "",
            "pepper_cross_summary": "",
            # Commercial intelligence
            "marketing_spend_estimate": "",
            "perf_mktg_signals":        "",
            "affiliate_likelihood":     "",
            "promo_intensity":          "",
            "commercial_intel_summary": "",
            "priority_tier":            "",
            # Contact (v2)
            "contact_title": "",
            "linkedin_url": "",
            "contact_authority": "other",
            "contact_role_match": False,
            # Legacy
            "pepper_summary": "",
            "business_fit_shoop": "",
            "business_fit_igraal": "",
            "business_fit_mydealz": "",
            "business_fit_gutscheine": "",
            "score_total": 0,
            "classification": "",
            "recommended_action": "",
        }

        try:
            lead_state = await _per_lead_graph.ainvoke(lead_state)
            state = {
                **state,
                "processed_leads": lead_state.get("processed_leads", []),
                "errors": lead_state.get("errors", []),
            }
        except Exception as exc:
            logger.error(
                "run_pipeline: Unerwarteter Fehler bei Lead '%s' @ '%s': %s",
                name, firma, exc, exc_info=True,
            )
            state["errors"] = [*state.get("errors", []), f"Lead '{name}' @ '{firma}': {exc}"]

    # ── Step 3: Write results ────────────────────────────────────────────────
    final_state = await write_results_node(state)

    processed = final_state.get("processed_leads", [])
    filtered = sum(1 for d in processed if d.get("classification") == "FILTERED")
    qualified = len(processed) - filtered
    errors = final_state.get("errors", [])

    logger.info(
        "run_pipeline: Fertig. %d qualifiziert, %d gefiltert, %d Fehler. Telegram: %s",
        qualified, filtered, len(errors),
        "ja" if final_state.get("telegram_notified") else "nein",
    )
    if errors:
        logger.warning("run_pipeline: Fehler:\n%s", "\n".join(errors))

    return final_state
