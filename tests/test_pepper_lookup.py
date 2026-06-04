"""
Regression tests for Pepper JSON parsing — guards the bug where a Pepper reply
truncated mid-generation zeroed out the signal and forced strong leads to COLD.
"""
from app.agents.lead_qualifying.services import pepper_lookup as pl


def test_extract_json_strict():
    assert pl._extract_json('{"by_brand": {}, "total_mentions_all": 0}')["total_mentions_all"] == 0


def test_extract_json_fenced():
    assert pl._extract_json('```json\n{"a": 1}\n```')["a"] == 1


def test_extract_json_with_surrounding_prose():
    raw = 'Here you go:\n{"by_brand": {}, "brands_found": 0}\nHope that helps!'
    assert pl._extract_json(raw)["brands_found"] == 0


def test_extract_json_repairs_truncation():
    # Reply cut off mid-"mx" country block (the real TEMU failure)
    truncated = (
        '{"by_brand":{"Temu":{"total_mentions":226,"total_deals":89,"by_country":{'
        '"pl":{"pos":32,"neu":37,"neg":27,"mixed":1,"total":97,"deals":21},'
        '"uk":{"pos":21,"neu":10,"neg":12,"mixed":2,"total":45,"deals":20},'
        '"de":{"pos":8,"neu":9,"neg":15,"mixed":0,"total":32,"deals":18},'
        '"mx":{"pos":11,"neu":7,"neg":5,'
    )
    parsed = pl._extract_json(truncated)
    assert parsed is not None
    countries = parsed["by_brand"]["Temu"]["by_country"]
    assert set(countries) == {"pl", "uk", "de"}     # complete prefix kept, partial mx dropped
    mentions = sum(c["total"] for c in countries.values())
    assert mentions == 174


def test_extract_json_unrecoverable_returns_none():
    assert pl._extract_json("totally not json at all") is None


def test_repair_handles_braces_inside_strings():
    # A string value containing braces must not confuse the bracket balancer
    raw = '{"name": "weird {value} here", "n": 5}'
    assert pl._extract_json(raw)["n"] == 5
