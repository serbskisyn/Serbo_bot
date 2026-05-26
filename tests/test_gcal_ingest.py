"""Tests for the calendar-event → todo ingest (no LLM)."""
import pytest

from app.services import gcal_ingest, todos as todos_svc


# ── Title matcher ───────────────────────────────────────────────────────────


def test_matches_prep_obvious_keywords():
    assert gcal_ingest._matches_prep("Q3 Roadmap Review") is not None
    assert gcal_ingest._matches_prep("Vorbereiten: Pitch CSO") is not None
    assert gcal_ingest._matches_prep("Demo für Sales") is not None
    assert gcal_ingest._matches_prep("Kickoff Granola-Migration") is not None
    assert gcal_ingest._matches_prep("Interview Backend-Hire") is not None


def test_matches_prep_ignores_recurring_or_blockers():
    assert gcal_ingest._matches_prep("Daily Standup") is None
    assert gcal_ingest._matches_prep("Lunch with Kim") is None
    assert gcal_ingest._matches_prep("Focus Block") is None
    assert gcal_ingest._matches_prep("Urlaub") is None
    assert gcal_ingest._matches_prep("Deep Work") is None


def test_matches_prep_ignores_random_titles():
    assert gcal_ingest._matches_prep("Coffee Chat") is None
    assert gcal_ingest._matches_prep("Random Meeting") is None
    assert gcal_ingest._matches_prep("") is None


def test_matches_prep_standup_overrides_keyword():
    # "Daily" is ignored even if the title also contains a prep keyword
    assert gcal_ingest._matches_prep("Daily Review") is None


# ── End-to-end (with mock get_events) ───────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(todos_svc, "TODOS_DB", tmp_path / "todos.db")
    monkeypatch.setattr(gcal_ingest, "GCAL_CALENDAR_ID_1", "primary@example.com")
    monkeypatch.setattr(gcal_ingest, "GCAL_CALENDAR_ID_2", "")


@pytest.mark.anyio
async def test_ingest_creates_todos_for_matched_events(monkeypatch):
    fake_events = [
        {"summary": "Q3 Roadmap Review",
         "start": {"dateTime": "2099-01-15T10:00:00+02:00"}},
        {"summary": "Daily Standup",
         "start": {"dateTime": "2099-01-15T09:00:00+02:00"}},
        {"summary": "Pitch CSO vorbereiten",
         "start": {"dateTime": "2099-01-20T14:00:00+02:00"}},
    ]

    def fake_get_events(cal_id, start=None, end=None, max_results=50):
        return fake_events

    import app.services.gcal_client as gcal_client
    monkeypatch.setattr(gcal_client, "get_events", fake_get_events)

    result = await gcal_ingest.ingest_for_user(1, days_ahead=365)
    assert result["scanned"] == 3
    assert result["matched"] == 2
    assert result["added"] == 2

    rows = await todos_svc.list_todos(1, scope="all")
    titles = {r["text"] for r in rows}
    assert "Vorbereiten: Q3 Roadmap Review" in titles
    assert "Vorbereiten: Pitch CSO vorbereiten" in titles


@pytest.mark.anyio
async def test_ingest_idempotent_on_rerun(monkeypatch):
    fake_events = [
        {"summary": "Pitch Demo", "start": {"dateTime": "2099-02-15T10:00:00+02:00"}},
    ]

    def fake_get_events(cal_id, start=None, end=None, max_results=50):
        return fake_events

    import app.services.gcal_client as gcal_client
    monkeypatch.setattr(gcal_client, "get_events", fake_get_events)

    first = await gcal_ingest.ingest_for_user(1, days_ahead=365)
    second = await gcal_ingest.ingest_for_user(1, days_ahead=365)

    assert first["added"] == 1
    assert second["added"] == 0
    assert second["mentioned"] == 1


@pytest.mark.anyio
async def test_ingest_skips_when_no_calendars(monkeypatch):
    monkeypatch.setattr(gcal_ingest, "GCAL_CALENDAR_ID_1", "")
    monkeypatch.setattr(gcal_ingest, "GCAL_CALENDAR_ID_2", "")
    result = await gcal_ingest.ingest_for_user(1)
    assert result.get("skipped") == "no calendars"
