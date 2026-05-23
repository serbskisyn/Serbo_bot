"""
scorer_v2.py — Deterministic lead score (0-100), revised composition.

Score composition (total 100):
  A) Business profile   30   (Model 12 + Size 10 + Brand-Count 8)
  B) Pepper signal      40   (Volume-target 20 + Sentiment 10 + Cross-country 10)
  C) Contact            15   (Seniority 8 + Authority 4 + LinkedIn 2 + Role-Match 1)
  D) Sales context      15   (Market-Overlap 8 + Sales-Signals 7)

Override rules:
  HARD: No Pepper signal at all (target + cross both 0) → max COLD (cap 39)
  Micro-signal: ≤20 total mentions AND 0 positive across all brands → cap at COLD
  Auto-HOT: target_total > 2000 AND pos_rate ≥ 0.55 → forces HOT (min 70)
  Auto-COLD: B2B/Manufacturer AND 0 brands AND 0 mentions

Classification thresholds: HOT ≥ 70 | WARM 40-69 | COLD < 40
"""
from __future__ import annotations

from typing import Literal

# ── Atolls market lists per brand (user spec 2026-05-20) ─────────────────────
ATOLLS_MARKETS_SHOOP   = frozenset({"de"})
ATOLLS_MARKETS_IGRAAL  = frozenset({"fr", "pl", "de", "es"})
ATOLLS_MARKETS_MYDEALZ = frozenset({"de", "at", "fr", "nl", "pl", "es", "uk", "us", "se"})
ATOLLS_MARKETS_ALL     = ATOLLS_MARKETS_SHOOP | ATOLLS_MARKETS_IGRAAL | ATOLLS_MARKETS_MYDEALZ


# ── A) Business profile (30 pts) ─────────────────────────────────────────────

def _business_model_score(model: str) -> int:
    """max 12"""
    m = (model or "").lower()
    if "marketplace" in m or "hybrid" in m:
        return 12
    if "b2c" in m or "d2c" in m:
        return 10
    if "manufacturer" in m:
        return 6
    if "b2b" in m:
        return 2
    return 0


def _size_score(employees: str, revenue: str) -> int:
    """max 10 — higher of employee-bucket or revenue-bucket."""
    emp = (employees or "").lower()
    rev = (revenue or "").lower()

    def emp_bucket() -> int:
        if ">1000" in emp or "1000+" in emp or "10000" in emp:               return 10
        if "200-1000" in emp or "500-1000" in emp or "200-500" in emp \
                or "501-1.000" in emp or "501-1000" in emp:                  return 8
        if "50-200" in emp or "100-200" in emp or "50-100" in emp:           return 5
        if "10-50" in emp or "1-50" in emp:                                  return 2
        if "1-10" in emp:                                                    return 2
        return 1 if emp and emp not in ("unbekannt", "unknown") else 0

    def rev_bucket() -> int:
        if ">1b" in rev or "1b+" in rev or ">1 b" in rev:                    return 10
        if "200m-1b" in rev or ">200m" in rev:                               return 9
        if "50-200m" in rev or "100-200m" in rev:                            return 7
        if "10-50m" in rev or "20-50m" in rev:                               return 5
        if "<10m" in rev or "1-10m" in rev:                                  return 2
        return 1 if rev and rev not in ("unbekannt", "unknown") else 0

    return max(emp_bucket(), rev_bucket())


def _brand_count_score(validated_brands: list) -> int:
    """max 8"""
    n = len([b for b in (validated_brands or []) if isinstance(b, dict) and b.get("name")])
    if n >= 5: return 8
    if n >= 2: return 6
    if n == 1: return 3
    return 0


# ── B) Pepper signal (40 pts) ────────────────────────────────────────────────

def _target_volume_score(target_total: int) -> int:
    """max 20"""
    if target_total >= 500: return 20
    if target_total >= 100: return 15
    if target_total >= 20:  return 10
    if target_total >= 5:   return 5   # < 5 is noise floor
    return 0


def _sentiment_health_score(pos_rate: float | None) -> int:
    """max 10 — pos_rate-based bucket. None (no data) → neutral 5."""
    if pos_rate is None: return 5
    if pos_rate >= 0.65: return 10
    if pos_rate >= 0.50: return 7
    if pos_rate >= 0.35: return 3
    return 0


def _cross_country_score(by_brand: dict, exclude_iso: str | None) -> int:
    """max 10 — number of non-target Pepper countries with ≥50 mentions."""
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


def _total_pepper_volume(by_brand: dict) -> int:
    """Sum of all mentions across all brands × countries — for the no-signal override."""
    total = 0
    for stats in by_brand.values():
        for c in (stats.get("by_country") or {}).values():
            total += int(c.get("total") or 0)
    return total


# ── C) Contact signal (15 pts) ───────────────────────────────────────────────

def _seniority_score(contact_seniority: str) -> int:
    """max 8"""
    s = (contact_seniority or "").lower()
    if "senior" in s or "c-level" in s or "founder" in s: return 8
    if "mid" in s or "manager" in s:                      return 4
    if "junior" in s:                                     return 1
    return 4  # unknown → neutral


def _authority_score(authority: str) -> int:
    """max 4"""
    a = (authority or "").lower()
    if "decision" in a: return 4
    if "influencer" in a: return 2
    return 0


def _linkedin_score(linkedin_url: str) -> int:
    """max 2 — having a LinkedIn URL is a trust signal."""
    return 2 if linkedin_url and "linkedin.com" in linkedin_url.lower() else 0


def _role_match_score(role_match: bool) -> int:
    """max 1 — marketing/sales/eCom role match."""
    return 1 if role_match else 0


# ── D) Sales context (15 pts) ────────────────────────────────────────────────

def _market_overlap_score(primary_markets: list, atolls_markets: frozenset) -> int:
    """max 8 — overlap of primary_markets with Atolls union."""
    if not primary_markets:
        return 0
    mkts = {m.lower().strip() for m in primary_markets if isinstance(m, str)}
    overlap = mkts & atolls_markets
    if len(overlap) >= 3: return 8
    if len(overlap) >= 1: return 5
    return 0


def _sales_signal_score(sales_signals: str) -> int:
    """max 7 — keyword density in sales_signals text."""
    text = (sales_signals or "").strip().lower()
    if not text or text in ("(keine)", "keine", "—", "(none)", "none"):
        return 0
    keywords = (
        "growth", "wachstum", "funding", "series", "investment",
        "expansion", "expand", "launch",
        "affiliate", "partner", "performance marketing", "performance-marketing",
        "cashback", "coupon", "gutschein", "deal", "campaign", "kampagne",
    )
    hits = sum(1 for kw in keywords if kw in text)
    if hits >= 3 and len(text) >= 100: return 7
    if hits >= 2:                       return 5
    if hits >= 1:                       return 3
    if len(text) >= 80:                 return 2
    return 1


# ── Main scoring function ─────────────────────────────────────────────────────

Classification = Literal["HOT", "WARM", "COLD"]


def compute_score(state: dict) -> dict:
    by_brand   = state.get("pepper_by_brand") or {}
    target_iso = (state.get("target_country_iso") or "").lower()

    # Target-country aggregate
    target_pos = target_neg = target_neu = 0
    if target_iso:
        for stats in by_brand.values():
            c = (stats.get("by_country") or {}).get(target_iso)
            if c:
                target_pos += int(c.get("pos") or 0)
                target_neg += int(c.get("neg") or 0)
                target_neu += int(c.get("neu") or 0)
    target_total = target_pos + target_neg + target_neu
    target_pos_rate = (
        target_pos / (target_pos + target_neg)
        if (target_pos + target_neg) > 0 else None
    )

    total_pepper = _total_pepper_volume(by_brand)

    # Components
    b_model       = _business_model_score(state.get("business_model", ""))
    b_size        = _size_score(state.get("company_employees", ""), state.get("company_revenue", ""))
    b_brand_count = _brand_count_score(state.get("validated_brands") or [])

    p_volume      = _target_volume_score(target_total)
    p_sentiment   = _sentiment_health_score(target_pos_rate)
    p_cross       = _cross_country_score(by_brand, exclude_iso=target_iso or None)

    c_seniority   = _seniority_score(state.get("contact_seniority", ""))
    c_authority   = _authority_score(state.get("contact_authority", ""))
    c_linkedin    = _linkedin_score(state.get("linkedin_url", ""))
    c_role        = _role_match_score(bool(state.get("contact_role_match", False)))

    s_overlap     = _market_overlap_score(state.get("primary_markets") or [], ATOLLS_MARKETS_ALL)
    s_signals     = _sales_signal_score(state.get("sales_signals", ""))

    score = (b_model + b_size + b_brand_count
             + p_volume + p_sentiment + p_cross
             + c_seniority + c_authority + c_linkedin + c_role
             + s_overlap + s_signals)
    score = max(0, min(100, score))

    override = ""
    classification: Classification = "COLD"

    # ── Override rules (in priority order) ────────────────────────────────────
    # HARD RULE: no Pepper signal at all → max COLD (user requirement)
    if total_pepper == 0:
        classification = "COLD"
        override = "No Pepper signal in target or cross-country — capped at COLD"
        if score > 39:
            score = 39
    # Micro-signal: ≤20 total mentions + zero positive across all brands/countries → noise
    elif total_pepper <= 20:
        total_all_pos = sum(
            int(c.get("pos") or 0)
            for stats in by_brand.values()
            for c in (stats.get("by_country") or {}).values()
        )
        if total_all_pos == 0:
            classification = "COLD"
            override = f"Micro Pepper signal ({total_pepper}m, 0 positive) — no actionable community presence, capped at COLD"
            if score > 39:
                score = 39
        elif score >= 70:   classification = "HOT"
        elif score >= 40:   classification = "WARM"
        else:               classification = "COLD"
    # Auto-HOT: target >2000 mentions AND pos_rate ≥ 55%
    elif target_total > 2000 and target_pos_rate is not None and target_pos_rate >= 0.55:
        classification = "HOT"
        override = f"Auto-HOT: {target_total} mentions in target country, {target_pos_rate*100:.0f}% positive"
        if score < 70:
            score = 70
    else:
        # Auto-COLD: B2B/Manufacturer + 0 brands + 0 pepper
        biz = (state.get("business_model") or "").lower()
        no_brands = len(state.get("validated_brands") or []) == 0
        if no_brands and total_pepper == 0 and ("b2b" in biz or "manufacturer" in biz):
            classification = "COLD"
            override = "Auto-COLD: B2B/Manufacturer with no brands and no Pepper signal"
            if score > 39:
                score = 39
        else:
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
        "contact_seniority":   c_seniority,
        "contact_authority":   c_authority,
        "contact_linkedin":    c_linkedin,
        "contact_role_match":  c_role,
        "market_overlap":      s_overlap,
        "sales_signals":       s_signals,
    }

    return {
        "score_total":     score,
        "classification":  classification,
        "breakdown":       breakdown,
        "override_reason": override,
    }


def format_breakdown(result: dict) -> str:
    """Compact one-line breakdown summary."""
    bd = result.get("breakdown", {})
    biz = bd.get("business_model", 0) + bd.get("size", 0) + bd.get("brand_count", 0)
    pep = bd.get("pepper_target_volume", 0) + bd.get("pepper_sentiment", 0) + bd.get("pepper_cross", 0)
    con = (bd.get("contact_seniority", 0) + bd.get("contact_authority", 0)
           + bd.get("contact_linkedin", 0) + bd.get("contact_role_match", 0))
    ctx = bd.get("market_overlap", 0) + bd.get("sales_signals", 0)
    s = f"Biz {biz}/30 · Pepper {pep}/40 · Contact {con}/15 · Ctx {ctx}/15"
    if result.get("override_reason"):
        s += f" · {result['override_reason']}"
    return s
