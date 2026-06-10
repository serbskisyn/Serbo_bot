"""
Regression tests for the lead scorer's Pepper handling — guards the bug where
a Pepper outage (auth drop / unparseable reply) was treated as a genuine
"0 mentions" and hard-capped strong leads to COLD.
"""
from app.agents.lead_qualifying.services.scorer_v2 import compute_score

_STRONG = dict(
    business_model="B2C",
    company_revenue="50-200M EUR",
    company_employees="500+",
    validated_brands=[{"name": f"b{i}"} for i in range(5)],
    primary_markets=["fr"],
    sales_signals="growth expansion affiliate cashback campaign",
    contact_seniority="mid",
    contact_authority="other",
    linkedin_url="",
    contact_role_match=False,
    pepper_by_brand={},
    target_country_iso="fr",
)


def test_pepper_unavailable_is_not_hard_capped():
    r = compute_score({**_STRONG, "pepper_unavailable": True})
    assert r["classification"] in ("WARM", "HOT")
    assert "unavailable" in r["override_reason"].lower()


def test_genuine_zero_pepper_is_hard_capped_cold():
    r = compute_score({**_STRONG, "pepper_unavailable": False})
    assert r["classification"] == "COLD"
    assert r["score_total"] <= 39


def test_b2b_skip_still_capped_cold():
    r = compute_score(dict(
        business_model="B2B", validated_brands=[], pepper_by_brand={},
        pepper_unavailable=False, company_employees="10-50",
    ))
    assert r["classification"] == "COLD"


def test_pepper_signal_still_scores_hot():
    # Real Pepper signal present → normal thresholds, no cap
    by_brand = {"Temu": {"by_country": {
        "fr": {"pos": 200, "neg": 50, "neu": 50, "total": 300, "deals": 40},
        "de": {"pos": 90, "neg": 20, "neu": 20, "total": 130, "deals": 15},
    }}}
    r = compute_score({**_STRONG, "pepper_by_brand": by_brand, "pepper_unavailable": False})
    assert r["classification"] in ("WARM", "HOT")
    assert r["score_total"] >= 40
