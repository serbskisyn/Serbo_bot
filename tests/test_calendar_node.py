"""Tests for the calendar chat node (gcal + LLM mocked)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents.nodes import calendar as cal_node

_BERLIN = ZoneInfo("Europe/Berlin")


# ── Time-window parsing ──────────────────────────────────────────────────────


def test_window_morgen():
    start, end, label = cal_node._window("habe ich morgen termine?")
    assert label == "morgen"
    assert (end - start).days == 1


def test_window_woche():
    start, end, label = cal_node._window("was steht diese woche an?")
    assert label == "diese Woche"
    assert (end - start).days == 7


def test_window_heute():
    start, end, label = cal_node._window("habe ich heute noch was?")
    assert label == "heute"
    assert (end - start).days == 1


def test_window_uebermorgen():
    start, end, label = cal_node._window("und übermorgen?")
    assert label == "übermorgen"


def test_window_open_ended_defaults_3_days():
    start, end, label = cal_node._window("meine termine bitte")
    assert (end - start).days == 3


# ── Event formatting ─────────────────────────────────────────────────────────


def test_fmt_event_timed():
    ev = {"summary": "1:1 Kim", "start": {"dateTime": "2026-05-27T10:00:00+02:00"}}
    out = cal_node._fmt_event(ev)
    assert "1:1 Kim" in out
    assert "10:00" in out


def test_fmt_event_allday():
    ev = {"summary": "Urlaub", "start": {"date": "2026-05-27"}}
    out = cal_node._fmt_event(ev)
    assert "Urlaub" in out
    assert "ganztägig" in out


# ── Node end-to-end (mocked) ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def cal_configured(monkeypatch):
    monkeypatch.setattr(cal_node, "GCAL_CALENDAR_ID_1", "primary@example.com")
    monkeypatch.setattr(cal_node, "GCAL_CALENDAR_ID_2", "")


@pytest.mark.anyio
async def test_calendar_node_answers_with_events(monkeypatch):
    fake_events = [
        {"summary": "Lead-Sync", "start": {"dateTime": "2026-05-27T14:30:00+02:00"}},
        {"summary": "1:1 Kim", "start": {"dateTime": "2026-05-27T10:00:00+02:00"}},
    ]
    import app.services.gcal_client as gcal_client
    monkeypatch.setattr(gcal_client, "get_events", lambda *a, **k: fake_events)

    async def fake_ask_llm(prompt, history=None, system_prompt=""):
        assert "Lead-Sync" in prompt and "1:1 Kim" in prompt
        return "Heute: 10:00 1:1 Kim, 14:30 Lead-Sync."

    monkeypatch.setattr(cal_node, "ask_llm", fake_ask_llm)

    state = {"user_id": 1, "text": "habe ich heute termine?", "messages": []}
    out = await cal_node.calendar_node(state)
    assert "Lead-Sync" in out["response"]


@pytest.mark.anyio
async def test_calendar_node_no_events(monkeypatch):
    import app.services.gcal_client as gcal_client
    monkeypatch.setattr(gcal_client, "get_events", lambda *a, **k: [])

    state = {"user_id": 1, "text": "termine morgen?", "messages": []}
    out = await cal_node.calendar_node(state)
    assert "Keine Termine" in out["response"]


@pytest.mark.anyio
async def test_calendar_node_no_calendar_configured(monkeypatch):
    monkeypatch.setattr(cal_node, "GCAL_CALENDAR_ID_1", "")
    monkeypatch.setattr(cal_node, "GCAL_CALENDAR_ID_2", "")
    state = {"user_id": 1, "text": "termine heute?", "messages": []}
    out = await cal_node.calendar_node(state)
    assert "Kein Kalender" in out["response"]


@pytest.mark.anyio
async def test_calendar_node_handles_fetch_error(monkeypatch):
    import app.services.gcal_client as gcal_client

    def boom(*a, **k):
        raise RuntimeError("API down")

    monkeypatch.setattr(gcal_client, "get_events", boom)
    state = {"user_id": 1, "text": "termine heute?", "messages": []}
    out = await cal_node.calendar_node(state)
    assert "❌" in out["response"]
