"""Tests for the weather service + node (network mocked)."""
import pytest

from app.agents.nodes import weather as weather_node_mod
from app.bot import profile
from app.services import weather as weather_svc


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile.yaml")
    profile._store.clear()
    yield
    profile._store.clear()


# ── WMO label map ────────────────────────────────────────────────────────────


def test_wmo_label_known_codes():
    assert weather_svc.wmo_label(0) == "klar"
    assert weather_svc.wmo_label(61) == "leichter Regen"
    assert weather_svc.wmo_label(95) == "Gewitter"


def test_wmo_label_unknown_and_none():
    assert "Code 123" in weather_svc.wmo_label(123)
    assert weather_svc.wmo_label(None) == "unbekannt"


# ── format_weather_context ───────────────────────────────────────────────────


def test_format_weather_context():
    w = {
        "location": "Berlin", "country": "Deutschland",
        "current": {"temp": 12, "feels_like": 10, "humidity": 70,
                    "precip": 0.0, "wind": 8, "code": 3, "label": "bedeckt"},
        "daily": [{"date": "2026-05-27", "code": 61, "label": "leichter Regen",
                   "tmin": 9, "tmax": 15, "precip_prob": 60}],
    }
    ctx = weather_svc.format_weather_context(w)
    assert "Berlin, Deutschland" in ctx
    assert "bedeckt" in ctx
    assert "leichter Regen" in ctx
    assert "Regenrisiko 60%" in ctx


# ── _resolve_location ────────────────────────────────────────────────────────


def test_resolve_location_from_query():
    assert weather_node_mod._resolve_location(1, "Wie ist das Wetter in München?") == "München"
    assert weather_node_mod._resolve_location(1, "Wetter für Hamburg morgen") == "Hamburg"


@pytest.mark.anyio
async def test_resolve_location_from_profile():
    await profile.set_scalar(1, "identity", "location", "Berlin")
    # No explicit place in the query → falls back to profile
    assert weather_node_mod._resolve_location(1, "wie ist das wetter heute?") == "Berlin"


def test_resolve_location_hard_default():
    # No place in query, no profile → Berlin
    assert weather_node_mod._resolve_location(999, "wie wird das wetter?") == "Berlin"


def test_resolve_location_ignores_generic_caps():
    # "Deutschland" should not be treated as a city override
    assert weather_node_mod._resolve_location(999, "Wetter in Deutschland") == "Berlin"


# ── weather_node end-to-end (mocked) ─────────────────────────────────────────


@pytest.mark.anyio
async def test_weather_node_uses_profile_location(monkeypatch):
    await profile.set_scalar(1, "identity", "location", "Berlin")

    captured = {}

    async def fake_get_weather(location):
        captured["location"] = location
        return {
            "location": location, "country": "Deutschland",
            "current": {"temp": 14, "feels_like": 13, "humidity": 65,
                        "precip": 0.0, "wind": 10, "code": 1, "label": "überwiegend klar"},
            "daily": [],
        }

    async def fake_ask_llm(prompt, history=None, system_prompt=""):
        return f"In Berlin sind es 14°C. (prompt enthält: {'Berlin' in prompt})"

    monkeypatch.setattr(weather_node_mod, "get_weather", fake_get_weather)
    monkeypatch.setattr(weather_node_mod, "ask_llm", fake_ask_llm)

    state = {"user_id": 1, "text": "wie ist das wetter heute?", "messages": []}
    out = await weather_node_mod.weather_node(state)
    assert captured["location"] == "Berlin"
    assert "14°C" in out["response"]


@pytest.mark.anyio
async def test_weather_node_handles_fetch_failure(monkeypatch):
    async def fake_get_weather(location):
        return None

    monkeypatch.setattr(weather_node_mod, "get_weather", fake_get_weather)
    state = {"user_id": 999, "text": "Wetter in Atlantis?", "messages": []}
    out = await weather_node_mod.weather_node(state)
    assert "konnte" in out["response"].lower() or "atlantis" in out["response"].lower()
