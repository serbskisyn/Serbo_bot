"""
rerun_lead_row.py — Reset and re-process a single lead by sheet row index.

Usage:
    python scripts/rerun_lead_row.py 90
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.lead_qualifying.services.sheets import (
    write_validation_for_row,
    read_inbound_leads,
)
from app.agents.lead_qualifying.graph import _per_lead_graph
from app.agents.lead_qualifying.state import LeadState
from app.agents.lead_qualifying.nodes.fetch_new_leads import _compute_lead_key
from app.agents.lead_qualifying.nodes.write_results import write_results_node


async def rerun_row(target_row: int) -> None:
    print(f"[rerun] Lese Inbound-Tab …")
    rows = await read_inbound_leads()

    lead = next((r for r in rows if r.get("_row_index") == target_row), None)
    if lead is None:
        print(f"[rerun] Zeile {target_row} nicht gefunden (max _row_index={max(r['_row_index'] for r in rows)})")
        return

    firma = lead.get("Firma", "(unbekannt)")
    print(f"[rerun] Gefunden: '{firma}' in Zeile {target_row}")

    # 1. Clear Validation_Date so fetch_new_leads picks it up
    print(f"[rerun] Setze Validation_Date zurück …")
    await write_validation_for_row(target_row, {"Validation_Date": ""})

    # 2. Inject lead key
    lead["_lead_key"] = _compute_lead_key(lead)

    # 3. Build lead state and run sub-graph
    print(f"[rerun] Starte Pipeline für '{firma}' …")
    lead_state: LeadState = {
        "raw_leads": [],
        "new_leads": [],
        "processed_leads": [],
        "errors": [],
        "current_lead": lead,
        "pre_qualify_label": "",
        "pre_qualify_reason": "",
        "contact_title": "",
        "linkedin_url": "",
        "company_website": "",
        "northdata_summary": "",
        "news_summary": "",
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
        "pepper_summary": "",
        "business_fit_shoop": "",
        "business_fit_igraal": "",
        "business_fit_mydealz": "",
        "business_fit_gutscheine": "",
        "score_total": 0,
        "classification": "",
        "recommended_action": "",
        "_employee_count_estimate": "",
        "contact_authority": "other",
        "contact_role_match": False,
    }

    result = await _per_lead_graph.ainvoke(lead_state)
    print(f"[rerun] Graph fertig: classification={result.get('classification')} score={result.get('score_total')} pepper={result.get('pepper_summary')!r}")

    # 4. Write results to sheet
    print(f"[rerun] Schreibe Ergebnisse ins Sheet …")
    final = await write_results_node(result)
    errors = final.get("errors", [])
    if errors:
        print(f"[rerun] Fehler: {errors}")
    else:
        print(f"[rerun] Fertig. Sheet aktualisiert.")


if __name__ == "__main__":
    row = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    asyncio.run(rerun_row(row))
