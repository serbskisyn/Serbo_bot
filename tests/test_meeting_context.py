"""Tests for the meeting-context note parser shared across renderers."""
import pytest

from app.bot import profile
from app.services import briefing, evening_reflection, todos as todos_svc


# ── Pure helper ──────────────────────────────────────────────────────────────


def test_parse_meeting_context_canonical():
    out = todos_svc.parse_meeting_context("aus Meeting: Clicky Migration Team Daily (2026-05-26)")
    assert out == ("Clicky Migration Team Daily", "2026-05-26")


def test_parse_meeting_context_trims_whitespace():
    out = todos_svc.parse_meeting_context("  aus Meeting:  1:1 Kim  (2026-05-24)  ")
    assert out == ("1:1 Kim", "2026-05-24")


def test_parse_meeting_context_none_for_other_notes():
    assert todos_svc.parse_meeting_context("just a manual note") is None
    assert todos_svc.parse_meeting_context("") is None
    assert todos_svc.parse_meeting_context(None) is None


# ── Briefing surfaces meeting context for granola todos ──────────────────────


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(todos_svc, "TODOS_DB", tmp_path / "todos.db")
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile.yaml")
    monkeypatch.setattr(briefing, "GCAL_CALENDAR_ID_1", "")
    monkeypatch.setattr(briefing, "GCAL_CALENDAR_ID_2", "")
    monkeypatch.setattr(evening_reflection, "_SUMMARY_DIR", tmp_path / "summaries")
    profile._store.clear()
    yield
    profile._store.clear()


@pytest.mark.anyio
async def test_briefing_renders_meeting_context_for_open_todo():
    from datetime import date
    await todos_svc.add_todo(
        1,
        "Send tracking links for program validation testing",
        source="granola",
        due_date=date.today().isoformat(),
        notes="aus Meeting: Clicky Migration Team Daily (2026-05-26)",
    )
    text = await briefing.assemble_briefing(1)
    assert "Send tracking links" in text
    assert "Meeting: Clicky Migration Team Daily" in text


@pytest.mark.anyio
async def test_briefing_groups_decisions_by_meeting():
    await todos_svc.add_todo(
        1, "Entscheidung: Hard-code Amazon exclusions",
        source="granola",
        notes="aus Meeting: Clicky Migration Team Daily (2026-05-26)",
    )
    await todos_svc.add_todo(
        1, "Entscheidung: Roll back vendor-ID tracking",
        source="granola",
        notes="aus Meeting: Clicky Migration Team Daily (2026-05-26)",
    )
    await todos_svc.add_todo(
        1, "Entscheidung: Q3 OKRs draft approved",
        source="granola",
        notes="aus Meeting: 1:1 Kim (2026-05-26)",
    )
    text = await briefing.assemble_briefing(1)
    # Both meetings show up as separate groups
    assert "Clicky Migration Team Daily" in text
    assert "1:1 Kim" in text
    # And each decision is rendered
    assert "Hard-code Amazon" in text
    assert "Q3 OKRs draft" in text


# ── Evening reflection surfaces meeting context ──────────────────────────────


@pytest.mark.anyio
async def test_reflection_renders_meeting_for_open_todo():
    await todos_svc.add_todo(
        1, "Architecture-Doc finalisieren",
        source="granola",
        notes="aus Meeting: Eng Sync (2026-05-26)",
    )
    text = await evening_reflection.assemble_evening_reflection(1)
    assert "Architecture-Doc" in text
    assert "Meeting: Eng Sync" in text


@pytest.mark.anyio
async def test_reflection_groups_decisions_by_meeting():
    await todos_svc.add_todo(
        1, "Entscheidung: Use cookie checks",
        source="granola",
        notes="aus Meeting: Eng Sync (2026-05-26)",
    )
    await todos_svc.add_todo(
        1, "Entscheidung: Magazine entity required",
        source="granola",
        notes="aus Meeting: Eng Sync (2026-05-26)",
    )
    text = await evening_reflection.assemble_evening_reflection(1)
    assert text.count("Eng Sync") >= 1
    assert "Use cookie checks" in text
    assert "Magazine entity" in text
