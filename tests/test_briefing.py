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
    # No sweep history during tests (each test patches it explicitly if needed)
    monkeypatch.setattr(briefing, "SWEEP_HISTORY_FILE", tmp_path / "sweep_history.jsonl")
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


# ── Backtest-Pulse (sweep history) ───────────────────────────────────────────


def test_read_latest_sweep_missing_file():
    assert briefing._read_latest_sweep() is None


def test_read_latest_sweep_returns_last_line(tmp_path, monkeypatch):
    import json
    path = tmp_path / "sweep_history.jsonl"
    path.write_text(
        json.dumps({"date": "2026-05-27", "best_by_kelly": {"trail_pct": 0.030, "r": 0.92, "kelly": -0.05, "win_rate": 0.48}}) + "\n"
        + json.dumps({"date": "2026-05-28", "best_by_kelly": {"trail_pct": 0.015, "r": 1.03, "kelly": 0.003, "win_rate": 0.494}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(briefing, "SWEEP_HISTORY_FILE", path)
    out = briefing._read_latest_sweep()
    assert out["date"] == "2026-05-28"


def test_format_sweep_block_marginal_edge():
    s = {"date": "2026-05-28",
         "best_by_kelly": {"trail_pct": 0.015, "r": 1.03, "win_rate": 0.494, "kelly": 0.003}}
    block = briefing._format_sweep_block(s)
    assert "Backtest-Pulse" in block
    assert "2026-05-28" in block
    assert "marginaler Edge" in block


def test_format_sweep_block_no_edge():
    s = {"best_by_kelly": {"trail_pct": 0.03, "r": 0.9, "win_rate": 0.48, "kelly": -0.08}}
    block = briefing._format_sweep_block(s)
    assert "kein Edge" in block


def test_format_sweep_block_clear_edge():
    s = {"best_by_kelly": {"trail_pct": 0.025, "r": 3.5, "win_rate": 0.42, "kelly": 0.25}}
    block = briefing._format_sweep_block(s)
    assert "Edge messbar" in block


@pytest.mark.anyio
async def test_briefing_includes_sweep_block_when_file_present(tmp_path, monkeypatch):
    import json
    path = tmp_path / "sweep_history.jsonl"
    path.write_text(
        json.dumps({"date": "2026-05-28",
                    "best_by_kelly": {"trail_pct": 0.015, "r": 1.03, "win_rate": 0.494, "kelly": 0.003}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(briefing, "SWEEP_HISTORY_FILE", path)
    text = await briefing.assemble_briefing(1)
    assert "Backtest-Pulse" in text
    assert "1.5%" in text  # the trail_pct rendering
