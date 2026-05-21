"""
scorer_v2.py — Deterministischer Lead-Score (0-100) gemäß Spec vom 2026-05-20.

Score-Komposition:
  A) Business-Profil      max  35  (Modell 15 + Größe 10 + Brand-Count 10)
  B) Pepper-Signal        max  40  (Volumen-ZL 20 + Sentiment-Health 10 + Cross-Country 10)
  C) Markt + Kontext      max  25  (Markt-Überschneidung 10 + Sales-Signal 10 + Seniority 5)

Plus Override-Regeln:
  - Auto-HOT: Pepper-Volumen Zielland > 2000 UND Pos-Rate ≥ 55 %
  - Auto-COLD: Business-Modell ∈ {B2B, Manufacturer-Direct} UND 0 Brands UND 0 Mentions

Klassifikation:
  HOT  ≥ 70    WARM 40-69    COLD 15-39    (< 15 wird zu COLD)
"""
from __future__ import annotations

from typing import Literal

# ── Atolls-Markt-Listen pro Brand (User-Spec) ────────────────────────────────
ATOLLS_MARKETS_SHOOP    = frozenset({"de"})
ATOLLS_MARKETS_IGRAAL   = frozenset({"fr", "pl", "de", "es"})
ATOLLS_MARKETS_MYDEALZ  = frozenset({"de", "at", "fr", "nl", "pl", "es", "uk", "us", "se"})

# Union aller Atolls-Märkte für die Markt-Überschneidungs-Bewertung
ATOLLS_MARKETS_ALL = ATOLLS_MARKETS_SHOOP | ATOLLS_MARKETS_IGRAAL | ATOLLS_MARKETS_MYDEALZ


# ── Bucket-Funktionen ─────────────────────────────────────────────────────────

def _business_model_score(model: str) -> int:
    m = (model or "").lower()
    if "marketplace" in m or "hybrid" in m:
        return 15
    if "b2c" in m or "d2c" in m:
        return 12
    if "manufacturer" in m:
        return 8
    if "b2b" in m:
        return 3
    return 0  # unbekannt


def _size_score(employees: str, revenue: str) -> int:
    """Größenklasse aus MA + Umsatz (das höhere Signal gewinnt)."""
    emp = (employees or "").lower()
    rev = (revenue or "").lower()

    def emp_bucket() -> int:
        if ">1000" in emp or "1000+" in emp:                       return 10
        if "200-1000" in emp or "500-1000" in emp or "200-500" in emp: return 8
        if "50-200" in emp or "100-200" in emp or "50-100" in emp: return 5
        if "10-50" in emp or "1-50" in emp:                        return 2
        if "1-10" in emp:                                          return 2
        return 1 if emp else 0

    def rev_bucket() -> int:
        # Erkennt z.B. ">1B", "1B EUR", ">200M EUR", "50-200M EUR", "10-50M EUR", "<10M EUR"
        if ">1b" in rev or "1b+" in rev or ">1 b" in rev:          return 10
        if "200m-1b" in rev or ">200m" in rev:                     return 9
        if "50-200m" in rev or "100-200m" in rev:                  return 7
        if "10-50m" in rev or "20-50m" in rev:                     return 5
        if "<10m" in rev or "1-10m" in rev:                        return 2
        return 1 if rev else 0

    return max(emp_bucket(), rev_bucket())


def _brand_count_score(validated_brands: list) -> int:
    n = len([b for b in (validated_brands or []) if isinstance(b, dict) and b.get("name")])
    if n >= 5: return 10
    if n >= 2: return 7
    if n == 1: return 4
    return 0


# ── Pepper-Signal-Buckets ─────────────────────────────────────────────────────

def _target_volume_score(target_total: int) -> int:
    if target_total >= 500: return 20
    if target_total >= 100: return 15
    if target_total >= 20:  return 10
    if target_total >= 1:   return 5
    return 0


def _sentiment_health_score(pos_rate: float | None) -> int:
    if pos_rate is None: return 5  # neutral, weil keine Daten
    if pos_rate >= 0.65: return 10
    if pos_rate >= 0.50: return 7
    if pos_rate >= 0.35: return 3
    return 0


def _cross_country_score(by_brand: dict, exclude_iso: str | None) -> int:
    """Zählt Cross-Country-Märkte mit ≥50 Mentions (Pepper-Bezugspunkt)."""
    if not by_brand:
        return 0
    per_country: dict[str, int] = {}
    for stats in by_brand.values():
        for iso, c in (stats.get("by_country") or {}).items():
            if exclude_iso and iso == exclude_iso:
                continue
            per_country[iso] = per_country.get(iso, 0) + int(c.get("total") or 0)
    strong = sum(1 for total in per_country.values() if total >= 50)
    if strong >= 3: return 10
    if strong >= 1: return 5
    return 0


# ── Markt + Kontext ──────────────────────────────────────────────────────────

def _market_overlap_score(primary_markets: list, atolls_markets: frozenset) -> int:
    """Schnittmenge primary_markets ∩ Atolls-Märkte."""
    if not primary_markets:
        return 0
    mkts = {m.lower().strip() for m in primary_markets if isinstance(m, str)}
    overlap = mkts & atolls_markets
    if len(overlap) >= 3: return 10
    if len(overlap) >= 1: return 6
    return 0


def _sales_signal_score(sales_signals: str) -> int:
    """Heuristik: Länge + Schlüsselwort-Reichtum von sales_signals.

    Echte Sales-Signal-Bewertung wäre LLM-Aufgabe, aber wir halten's deterministisch.
    """
    text = (sales_signals or "").strip().lower()
    if not text or text in ("(keine)", "keine", "—"):
        return 0
    keywords = (
        "wachstum", "growth", "funding", "series", "investment",
        "expansion", "expand", "neue", "launch",
        "affiliate", "partner", "kooperation", "performance-marketing",
        "cashback", "gutschein", "deal", "kampagne",
    )
    hits = sum(1 for kw in keywords if kw in text)
    if hits >= 3 and len(text) >= 100: return 10
    if hits >= 2:                       return 7
    if hits >= 1:                       return 4
    if len(text) >= 80:                 return 3
    return 1


def _seniority_score(contact_seniority: str) -> int:
    s = (contact_seniority or "").lower()
    if "senior" in s or "c-level" in s or "founder" in s: return 10
    if "mid" in s or "manager" in s:                      return 5
    if "junior" in s:                                     return 2
    return 5  # unbekannt → neutral


# ── Hauptfunktion ─────────────────────────────────────────────────────────────

Classification = Literal["HOT", "WARM", "COLD"]


def compute_score(state: dict) -> dict:
    """Berechnet Score, Klassifikation und einen transparenten Breakdown aus state.

    Args:
      state: Der LeadState nach pepper_multi_country_node (alle Felder gefüllt)

    Returns:
      {
        "score_total": int 0-100,
        "classification": "HOT"|"WARM"|"COLD",
        "breakdown": {  # Pro-Komponente-Punkte
          "business_model": int, "size": int, "brand_count": int,
          "pepper_target_volume": int, "pepper_sentiment": int, "pepper_cross": int,
          "market_overlap": int, "sales_signals": int, "seniority": int,
        },
        "override_reason": str  # falls Auto-HOT/COLD getriggert
      }
    """
    by_brand        = state.get("pepper_by_brand") or {}
    target_iso      = (state.get("target_country_iso") or "").lower()
    by_country_target = {}
    if target_iso:
        for stats in by_brand.values():
            c = (stats.get("by_country") or {}).get(target_iso)
            if c:
                by_country_target.setdefault(target_iso, {"pos": 0, "neg": 0, "neu": 0})
                by_country_target[target_iso]["pos"] += int(c.get("pos") or 0)
                by_country_target[target_iso]["neg"] += int(c.get("neg") or 0)
                by_country_target[target_iso]["neu"] += int(c.get("neu") or 0)

    tc = by_country_target.get(target_iso, {})
    target_total = tc.get("pos", 0) + tc.get("neg", 0) + tc.get("neu", 0)
    target_pos_rate = (
        tc["pos"] / (tc["pos"] + tc["neg"])
        if tc.get("pos") and (tc.get("pos") + tc.get("neg")) > 0
        else None
    )

    # Komponenten-Scores
    b_model       = _business_model_score(state.get("business_model", ""))
    b_size        = _size_score(state.get("company_employees", ""),
                                state.get("company_revenue", ""))
    b_brand_count = _brand_count_score(state.get("validated_brands") or [])

    p_volume      = _target_volume_score(target_total)
    p_sentiment   = _sentiment_health_score(target_pos_rate)
    p_cross       = _cross_country_score(by_brand, exclude_iso=target_iso or None)

    m_overlap     = _market_overlap_score(state.get("primary_markets") or [],
                                          ATOLLS_MARKETS_ALL)
    m_signals     = _sales_signal_score(state.get("sales_signals", ""))
    m_seniority   = _seniority_score(state.get("contact_seniority", ""))

    score = (b_model + b_size + b_brand_count
             + p_volume + p_sentiment + p_cross
             + m_overlap + m_signals + m_seniority)
    score = max(0, min(100, score))

    # ── Override-Regeln ────────────────────────────────────────────────────────
    override = ""

    # Auto-HOT: Pepper-Volumen ZL > 2000 UND Pos-Rate ≥ 55 %
    if target_total > 2000 and target_pos_rate is not None and target_pos_rate >= 0.55:
        classification: Classification = "HOT"
        override = f"Auto-HOT: {target_total} Mentions im Zielland, {target_pos_rate*100:.0f}% pos"
        if score < 70:
            score = 70  # Score auf Mindestwert für HOT setzen
    else:
        # Auto-COLD: B2B/Manufacturer + 0 Brands + 0 Mentions
        biz = (state.get("business_model") or "").lower()
        no_brands = len(state.get("validated_brands") or []) == 0
        no_pepper = sum(s.get("total_mentions", 0) for s in by_brand.values()) == 0
        if no_brands and no_pepper and ("b2b" in biz or "manufacturer" in biz):
            classification = "COLD"
            override = "Auto-COLD: B2B/Manufacturer ohne Brands und ohne Pepper-Signal"
            if score > 39:
                score = 39
        else:
            # Normale Schwellen
            if score >= 70:   classification = "HOT"
            elif score >= 40: classification = "WARM"
            else:             classification = "COLD"

    breakdown = {
        "business_model":      b_model,
        "size":                b_size,
        "brand_count":         b_brand_count,
        "pepper_target_volume": p_volume,
        "pepper_sentiment":    p_sentiment,
        "pepper_cross":        p_cross,
        "market_overlap":      m_overlap,
        "sales_signals":       m_signals,
        "seniority":           m_seniority,
    }

    return {
        "score_total":    score,
        "classification": classification,
        "breakdown":      breakdown,
        "override_reason": override,
    }


def format_breakdown(result: dict) -> str:
    """Kurze 1-Zeilen-Darstellung des Score-Breakdowns für Notizen/Logs."""
    bd = result.get("breakdown", {})
    biz = bd.get("business_model", 0) + bd.get("size", 0) + bd.get("brand_count", 0)
    pep = bd.get("pepper_target_volume", 0) + bd.get("pepper_sentiment", 0) + bd.get("pepper_cross", 0)
    ctx = bd.get("market_overlap", 0) + bd.get("sales_signals", 0) + bd.get("seniority", 0)
    s = f"Biz {biz}/35 · Pepper {pep}/40 · Ctx {ctx}/25"
    if result.get("override_reason"):
        s += f" · {result['override_reason']}"
    return s
