"""Tests for briefing.assemble_briefing — uses mocked calendar + real todos.db + profile."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.bot import profile
from app.services import briefing, todos as todos_svc


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(todos_svc, "TODOS_DB", tmp_path / "todos.db")
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile.yaml")
    monkeypatch.setattr(briefing, "GCAL_CALENDAR_ID_1", "")
    monkeypatch.setattr(briefing, "GCAL_CALENDAR_ID_2", "")
    profile._store.clear()
    yield
    profile._store.clear()


# ── Helpers ──────────────────────────────────────────────────────────────────


def test_today_de_returns_german_weekday():
    s = briefing._today_de(date(2026, 5, 26))  # Tuesday
    assert "Dienstag" in s
    assert "26.05.2026" in s


def test_fmt_due_short_today():
    assert briefing._fmt_due_short(date.today().isoformat()) == "heute"


def test_fmt_due_short_overdue():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert "überfällig" in briefing._fmt_due_short(yesterday)


def test_fmt_due_short_tomorrow():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert briefing._fmt_due_short(tomorrow) == "morgen"


def test_fmt_due_short_none():
    assert briefing._fmt_due_short(None) == ""


def test_fmt_due_short_far_future():
    far = (date.today() + timedelta(days=30)).isoformat()
    assert "." in briefing._fmt_due_short(far)


def test_source_badge():
    assert briefing._source_badge("chat") == " 💬"
    assert briefing._source_badge("granola") == " 🗣"
    assert briefing._source_badge("gcal") == " 🗓"
    assert briefing._source_badge("manual") == ""


# ── Relationship alerts ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_relationship_alerts_threshold():
    long_ago = (date.today() - timedelta(days=30)).isoformat()
    recent = (date.today() - timedelta(days=2)).isoformat()
    await profile.add_dict_item(1, "people", {"name": "Andi", "last_mentioned": long_ago})
    await profile.add_dict_item(1, "people", {"name": "Kim", "last_mentioned": recent})

    alerts = briefing._relationship_alerts(1, threshold_days=21)
    names = {a[0] for a in alerts}
    assert "Andi" in names
    assert "Kim" not in names


@pytest.mark.anyio
async def test_relationship_alerts_empty():
    assert briefing._relationship_alerts(1, threshold_days=21) == []


# ── End-to-end briefing ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_briefing_empty_user_still_renders():
    text = await briefing.assemble_briefing(99)
    assert "Guten Morgen" in text
    assert "Keine" in text  # "Keine Termine" + "Keine offenen Todos"


@pytest.mark.anyio
async def test_briefing_includes_identity_name():
    await profile.set_scalar(1, "identity", "name", "Benno")
    text = await briefing.assemble_briefing(1)
    assert "Benno" in text


@pytest.mark.anyio
async def test_briefing_lists_top_todos():
    today = date.today().isoformat()
    await todos_svc.add_todo(1, "Deck finalisieren", due_date=today, source="manual")
    await todos_svc.add_todo(1, "Andi pingen", source="chat")
    text = await briefing.assemble_briefing(1)
    assert "Deck finalisieren" in text
    assert "Andi pingen" in text
    assert "Top Todos" in text


@pytest.mark.anyio
async def test_briefing_includes_yesterday_decisions():
    await todos_svc.add_todo(
        1, "Entscheidung: Migration auf Q3 verschoben",
        source="granola",
    )
    text = await briefing.assemble_briefing(1)
    assert "gestrigen Meetings" in text
    assert "Migration auf Q3 verschoben" in text


@pytest.mark.anyio
async def test_briefing_includes_relationship_alerts():
    long_ago = (date.today() - timedelta(days=40)).isoformat()
    await profile.add_dict_item(1, "people", {"name": "Andi", "last_mentioned": long_ago})
    text = await briefing.assemble_briefing(1)
    assert "Lange nichts gehört" in text
    assert "Andi" in text
    assert "40 Tage" in text


@pytest.mark.anyio
async def test_briefing_orders_overdue_first():
    """Briefing uses scope='today' (due_date <= today OR null) sorted by priority."""
    today = date.today()
    # Both must fall into the "today" scope (no future-only dates)
    await todos_svc.add_todo(1, "no_due_todo")  # NULL due_date → low priority
    await todos_svc.add_todo(1, "overdue_todo", due_date=(today - timedelta(days=2)).isoformat())
    text = await briefing.assemble_briefing(1)
    # overdue should appear before the undated one
    assert text.index("overdue_todo") < text.index("no_due_todo")
