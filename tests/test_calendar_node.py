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
    monkeypatch.setattr(cal_node, "GCAL_CALENDAR_ID_1", "gmail@example.com")
    monkeypatch.setattr(cal_node, "GCAL_CALENDAR_ID_2", "workspace@example.com")


def test_configured_calendars_both(monkeypatch):
    monkeypatch.setattr(cal_node, "GCAL_CALENDAR_ID_1", "a@x.com")
    monkeypatch.setattr(cal_node, "GCAL_CALENDAR_ID_2", "b@x.com")
    cals = cal_node._configured_calendars()
    assert [c[0] for c in cals] == ["a@x.com", "b@x.com"]
    assert [c[1] for c in cals] == ["Atolls (Arbeit)", "Bennoschwede@gmail.com"]


def test_configured_calendars_only_one(monkeypatch):
    monkeypatch.setattr(cal_node, "GCAL_CALENDAR_ID_1", "a@x.com")
    monkeypatch.setattr(cal_node, "GCAL_CALENDAR_ID_2", "")
    cals = cal_node._configured_calendars()
    assert len(cals) == 1


@pytest.mark.anyio
async def test_calendar_node_merges_both_calendars(monkeypatch):
    """Events from both calendars must be fetched, merged + sorted by start."""
    def fake_get_events(cal_id, start=None, end=None, max_results=25):
        if cal_id == "gmail@example.com":
            return [{"summary": "Lead-Sync", "start": {"dateTime": "2026-05-27T14:30:00+02:00"}}]
        if cal_id == "workspace@example.com":
            return [{"summary": "1:1 Kim", "start": {"dateTime": "2026-05-27T10:00:00+02:00"}}]
        return []

    import app.services.gcal_client as gcal_client
    monkeypatch.setattr(gcal_client, "get_events", fake_get_events)

    captured = {}

    async def fake_ask_llm(prompt, history=None, system_prompt=""):
        captured["prompt"] = prompt
        return "Heute: 10:00 1:1 Kim (Workspace), 14:30 Lead-Sync (Gmail)."

    monkeypatch.setattr(cal_node, "ask_llm", fake_ask_llm)

    state = {"user_id": 1, "text": "habe ich heute termine?", "messages": []}
    out = await cal_node.calendar_node(state)
    # Both events present in the LLM prompt
    assert "Lead-Sync" in captured["prompt"] and "1:1 Kim" in captured["prompt"]
    # Calendar labels attached
    assert "[Atolls (Arbeit)]" in captured["prompt"] and "[Bennoschwede@gmail.com]" in captured["prompt"]
    # Sorted: 10:00 1:1 Kim must appear before 14:30 Lead-Sync
    assert captured["prompt"].index("1:1 Kim") < captured["prompt"].index("Lead-Sync")
    assert "Lead-Sync" in out["response"]


@pytest.mark.anyio
async def test_calendar_node_one_calendar_errors_other_ok(monkeypatch):
    """If one calendar fails, the other's events still come through."""
    def fake_get_events(cal_id, start=None, end=None, max_results=25):
        if cal_id == "gmail@example.com":
            raise RuntimeError("gmail down")
        return [{"summary": "1:1 Kim", "start": {"dateTime": "2026-05-27T10:00:00+02:00"}}]

    import app.services.gcal_client as gcal_client
    monkeypatch.setattr(gcal_client, "get_events", fake_get_events)

    async def fake_ask_llm(prompt, history=None, system_prompt=""):
        return "Heute: 10:00 1:1 Kim."

    monkeypatch.setattr(cal_node, "ask_llm", fake_ask_llm)
    state = {"user_id": 1, "text": "termine heute?", "messages": []}
    out = await cal_node.calendar_node(state)
    assert "1:1 Kim" in out["response"]


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
async def test_calendar_node_all_calendars_error(monkeypatch):
    import app.services.gcal_client as gcal_client

    def boom(*a, **k):
        raise RuntimeError("API down")

    monkeypatch.setattr(gcal_client, "get_events", boom)
    state = {"user_id": 1, "text": "termine heute?", "messages": []}
    out = await cal_node.calendar_node(state)
    assert "❌" in out["response"]
