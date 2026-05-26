"""Tests for granola_sync — the orchestrator between MCP and our stores."""
import pytest

from app.bot import profile
from app.services import granola_sync, granola_lookup, todos as todos_svc


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(todos_svc, "TODOS_DB", tmp_path / "todos.db")
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile.yaml")
    profile._store.clear()
    yield
    profile._store.clear()


# ── granola_lookup JSON extraction (pure, no subprocess) ────────────────────


def test_extract_json_bare_object():
    raw = '{"meetings": []}'
    assert granola_lookup._extract_json(raw) == {"meetings": []}


def test_extract_json_with_markdown_fence():
    raw = "```json\n{\"meetings\": []}\n```"
    assert granola_lookup._extract_json(raw) == {"meetings": []}


def test_extract_json_with_surrounding_prose():
    raw = "Here is the result:\n\n{\"meetings\": [{\"title\": \"X\"}]}\n\nDone."
    out = granola_lookup._extract_json(raw)
    assert out and "meetings" in out


def test_extract_json_invalid_returns_none():
    assert granola_lookup._extract_json("nope") is None
    assert granola_lookup._extract_json("") is None


# ── End-to-end sync (with mocked lookup) ────────────────────────────────────


@pytest.mark.anyio
async def test_sync_creates_todos_and_people(monkeypatch):
    fake_payload = {
        "meetings": [
            {
                "title": "Eng-Sync",
                "date": "2026-05-24",
                "commitments": ["Architecture-Doc finalisieren", "Andi review pingen"],
                "decisions": ["Migration Q3 verschieben"],
                "mentioned_people": ["Andi", "Kim"],
            },
            {
                "title": "1:1 Kim",
                "date": "2026-05-25",
                "commitments": ["Q3 OKRs entwerfen"],
                "decisions": [],
                "mentioned_people": ["Kim"],
            },
        ],
        "error": None,
    }

    async def fake_lookup(lookback_hours=30, user_name=""):
        return fake_payload

    monkeypatch.setattr(granola_lookup, "get_recent_meetings", fake_lookup)

    counters = await granola_sync.sync_for_user(1, lookback_hours=24)
    assert counters["meetings"] == 2
    assert counters["commitments_added"] == 3
    assert counters["decisions_added"] == 1
    # Andi mentioned in one meeting, Kim in two — but add_dict_item dedupes
    # by name, so we expect 3 add calls total (2 Kim + 1 Andi)
    assert counters["people_added"] == 3

    rows = await todos_svc.list_todos(1, scope="all")
    texts = {r["text"] for r in rows}
    assert "Architecture-Doc finalisieren" in texts
    assert "Q3 OKRs entwerfen" in texts
    assert "Entscheidung: Migration Q3 verschieben" in texts

    people = profile.get_section(1, "people")
    names = {p["name"] for p in people}
    assert names == {"Andi", "Kim"}


@pytest.mark.anyio
async def test_sync_idempotent_on_rerun(monkeypatch):
    payload = {
        "meetings": [{
            "title": "Eng-Sync", "date": "2026-05-24",
            "commitments": ["Doc finalisieren"],
            "decisions": [], "mentioned_people": [],
        }],
        "error": None,
    }

    async def fake_lookup(lookback_hours=30, user_name=""):
        return payload

    monkeypatch.setattr(granola_lookup, "get_recent_meetings", fake_lookup)

    first = await granola_sync.sync_for_user(1)
    second = await granola_sync.sync_for_user(1)

    assert first["commitments_added"] == 1
    assert second["commitments_added"] == 0
    assert second["commitments_mentioned"] == 1


@pytest.mark.anyio
async def test_sync_returns_error_when_lookup_fails(monkeypatch):
    async def fake_lookup(lookback_hours=30, user_name=""):
        return {"meetings": [], "error": "subprocess: boom"}

    monkeypatch.setattr(granola_lookup, "get_recent_meetings", fake_lookup)
    out = await granola_sync.sync_for_user(1)
    assert out["error"] == "subprocess: boom"
    assert out["commitments_added"] == 0


@pytest.mark.anyio
async def test_sync_passes_profile_name_to_lookup(monkeypatch):
    """Identity name from profile must reach granola_lookup so the LLM can filter."""
    await profile.set_scalar(1, "identity", "name", "Benno")
    captured: dict = {}

    async def fake_lookup(lookback_hours=30, user_name=""):
        captured["user_name"] = user_name
        captured["lookback_hours"] = lookback_hours
        return {"meetings": [], "error": None}

    monkeypatch.setattr(granola_lookup, "get_recent_meetings", fake_lookup)
    await granola_sync.sync_for_user(1, lookback_hours=24)
    assert captured["user_name"] == "Benno"
    assert captured["lookback_hours"] == 24


@pytest.mark.anyio
async def test_sync_warns_and_continues_without_profile_name(monkeypatch, caplog):
    """No identity.name → still queries, but with empty user_name + warning."""
    captured: dict = {}

    async def fake_lookup(lookback_hours=30, user_name=""):
        captured["user_name"] = user_name
        return {"meetings": [], "error": None}

    monkeypatch.setattr(granola_lookup, "get_recent_meetings", fake_lookup)
    import logging
    with caplog.at_level(logging.WARNING):
        await granola_sync.sync_for_user(1)
    assert captured["user_name"] == ""
    assert any("no identity.name" in rec.message for rec in caplog.records)
