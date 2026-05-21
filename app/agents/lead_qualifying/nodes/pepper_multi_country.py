"""
pepper_multi_country.py — LangGraph node: Pepper-Lookup für alle validierten Brands,
aufgeschlüsselt nach Land. Zielland aus Inbound-Spalte "Target country" prominent.

Reads:  state["validated_brands"], state["current_lead"]["Quelle"] (= Target country)
Writes: state["pepper_by_brand"], state["pepper_brands_found"],
        state["pepper_total_mentions_all"], state["pepper_target_summary"],
        state["pepper_cross_summary"], state["target_country_iso"],
        state["pepper_summary"] (Legacy: Gesamt-Pepper-Summary)
"""
from __future__ import annotations

import logging

from app.agents.lead_qualifying.services.country_mapping import to_pepper_code
from app.agents.lead_qualifying.services.pepper_lookup import (
    get_multi_brand_sentiment,
    format_country_sentiment,
    format_cross_country_summary,
)
from app.agents.lead_qualifying.state import LeadState

logger = logging.getLogger(__name__)


def _format_legacy_summary(by_brand: dict, total: int) -> str:
    """Aggregate summary across all brands + countries (legacy column)."""
    if total <= 0 or not by_brand:
        return "No Pepper mentions"
    n_brands = len(by_brand)
    pos = neg = 0
    for stats in by_brand.values():
        for c in (stats.get("by_country") or {}).values():
            pos += int(c.get("pos") or 0)
            neg += int(c.get("neg") or 0)
    rate = pos / (pos + neg) if (pos + neg) > 0 else None
    parts = [f"{total}m ({n_brands}b)", f"{pos}↑/{neg}↓"]
    if rate is not None:
        parts.append(f"{rate*100:.0f}%↑")
    return " · ".join(parts)


async def pepper_multi_country_node(state: LeadState) -> LeadState:
    lead   = state.get("current_lead", {})
    firma  = str(lead.get("Firma", "")).strip()
    quelle = str(lead.get("Quelle", "")).strip()        # Sheet-Spalte H = Target country
    target_iso = to_pepper_code(quelle) or ""

    # Validated Brands haben Priorität, Fallback auf Discovered + Firma selbst
    brand_dicts = state.get("validated_brands") or state.get("discovered_brands") or []
    brand_names = [b.get("name", "").strip() for b in brand_dicts if isinstance(b, dict)]
    brand_names = [n for n in brand_names if n]

    # Deduplizieren: gleiche Brand-Namen mehrfach (Perplexity gibt oft "BAGSMART",
    # "BAGSMART EU", "BAGSMART DE" → für Pepper-ILIKE-Match identisch).
    # Wir behalten die Reihenfolge, aber unique nach lowercased-Hauptname.
    seen: set[str] = set()
    deduped: list[str] = []
    for n in brand_names:
        key = n.lower().strip()
        if key not in seen:
            seen.add(key)
            deduped.append(n)
    if len(deduped) < len(brand_names):
        logger.debug("pepper_multi_country: %d→%d Brands dedupliziert", len(brand_names), len(deduped))
    brand_names = deduped[:15]  # Cap auf 15, sonst wird der Pepper-SQL zu lang

    # Fallback: wenn keine Brands gefunden, mit Firmenname allein versuchen
    if not brand_names:
        brand_names = [firma] if firma else []

    if not brand_names:
        return {
            **state,
            "pepper_by_brand": {},
            "pepper_brands_found": 0,
            "pepper_total_mentions_all": 0,
            "pepper_target_summary": "—",
            "pepper_cross_summary": "—",
            "target_country_iso": target_iso,
            "pepper_summary": "No brand data",
        }

    logger.info("pepper_multi_country: '%s' — %d Brands, target=%s ('%s')",
                firma, len(brand_names), target_iso or "?", quelle)

    result = await get_multi_brand_sentiment(firma, brand_names)
    by_brand     = result.get("by_brand") or {}
    brands_found = int(result.get("brands_found") or 0)
    total_all    = int(result.get("total_mentions_all") or 0)
    lookup_error = result.get("error") or ""

    target_summary = format_country_sentiment(by_brand, target_iso) if target_iso else "—"
    # All-country matrix (no exclusion, no cap) — one line per country sorted by total
    cross_summary  = format_cross_country_summary(by_brand)

    if total_all > 0 or by_brand:
        legacy_summary = _format_legacy_summary(by_brand, total_all)
    elif lookup_error:
        # Distinguish genuine "no data" from a failed lookup so the sheet
        # makes it clear the lookup should be retried.
        if "session limit" in lookup_error.lower() or "session" in lookup_error.lower():
            legacy_summary = "⚠ Session limit — retry"
        elif "mcp" in lookup_error.lower() or "unavail" in lookup_error.lower():
            legacy_summary = "⚠ Pepper MCP unavailable — retry"
        else:
            legacy_summary = f"⚠ Lookup failed — retry"
        logger.warning("pepper_multi_country: '%s' — Pepper-Fehler: %s", firma, lookup_error[:120])
    else:
        legacy_summary = "No Pepper mentions"

    return {
        **state,
        "pepper_by_brand":            by_brand,
        "pepper_brands_found":        brands_found,
        "pepper_total_mentions_all":  total_all,
        "pepper_target_summary":      target_summary,
        "pepper_cross_summary":       cross_summary,
        "target_country_iso":         target_iso,
        "pepper_summary":             legacy_summary,
    }
