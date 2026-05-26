"""Tests for the chat-based todo extractor (LLM mocked)."""
import pytest

from app.services import todo_extractor, todos as todos_svc


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(todos_svc, "TODOS_DB", tmp_path / "todos.db")


# ── JSON parsing ────────────────────────────────────────────────────────────


def test_extract_json_clean():
    assert todo_extractor._extract_json('{"todos": []}') == {"todos": []}


def test_extract_json_with_prose():
    raw = "Here are the todos:\n{\"todos\": [{\"text\": \"X\"}]}"
    assert todo_extractor._extract_json(raw) == {"todos": [{"text": "X"}]}


def test_extract_json_invalid():
    assert todo_extractor._extract_json("nope") is None
    assert todo_extractor._extract_json("") is None


# ── End-to-end (with mocked LLM) ────────────────────────────────────────────


@pytest.mark.anyio
async def test_extract_creates_todos(monkeypatch):
    async def fake_llm(user_text, assistant_response):
        return '{"todos": [{"text": "Deck vorbereiten", "due_hint": "morgen"}, {"text": "Andi pingen", "due_hint": null}]}'

    monkeypatch.setattr(todo_extractor, "_call_llm", fake_llm)

    summary = await todo_extractor.extract_from_chat(1, "ich muss noch das Deck und Andi", "ok")
    assert summary["added"] == 2
    rows = await todos_svc.list_todos(1, scope="all")
    texts = {r["text"] for r in rows}
    assert "Deck vorbereiten" in texts
    assert "Andi pingen" in texts


@pytest.mark.anyio
async def test_extract_mentions_existing(monkeypatch):
    await todos_svc.add_todo(1, "Deck vorbereiten")

    async def fake_llm(user_text, assistant_response):
        return '{"todos": [{"text": "Deck vorbereiten", "due_hint": null}]}'

    monkeypatch.setattr(todo_extractor, "_call_llm", fake_llm)

    summary = await todo_extractor.extract_from_chat(1, "deck noch", "ok")
    assert summary["added"] == 0
    assert summary["mentioned"] == 1

    rows = await todos_svc.list_todos(1, scope="all")
    assert len(rows) == 1
    assert rows[0]["mention_count"] == 2


@pytest.mark.anyio
async def test_extract_handles_llm_failure(monkeypatch):
    async def fake_llm(user_text, assistant_response):
        raise RuntimeError("boom")

    monkeypatch.setattr(todo_extractor, "_call_llm", fake_llm)
    summary = await todo_extractor.extract_from_chat(1, "x", "y")
    assert summary == {"detected": 0, "added": 0, "mentioned": 0}


@pytest.mark.anyio
async def test_extract_caps_at_3_todos(monkeypatch):
    """LLM may suggest 10 — extractor should add at most 3."""
    # Use truly distinct phrases so semantic dedup (Phase 5) doesn't collapse them.
    distinct_texts = [
        "Brot kaufen",
        "Steuererklärung einreichen",
        "Auto zur Inspektion bringen",
        "Geschenk für Mama besorgen",
        "Domain renewal verlängern",
        "Backup-Festplatte tauschen",
        "Pull-Request reviewen",
        "Architektur-Dokumentation aktualisieren",
        "Mitarbeitergespräch vorbereiten",
        "Reisepass beantragen",
    ]
    async def fake_llm(user_text, assistant_response):
        items = ", ".join(
            f'{{"text": "{t}", "due_hint": null}}' for t in distinct_texts
        )
        return f'{{"todos": [{items}]}}'

    monkeypatch.setattr(todo_extractor, "_call_llm", fake_llm)
    summary = await todo_extractor.extract_from_chat(1, "x", "y")
    assert summary["added"] == 3


@pytest.mark.anyio
async def test_extract_skips_very_short_texts(monkeypatch):
    async def fake_llm(user_text, assistant_response):
        return '{"todos": [{"text": "ok", "due_hint": null}, {"text": "Brot kaufen", "due_hint": null}]}'

    monkeypatch.setattr(todo_extractor, "_call_llm", fake_llm)
    summary = await todo_extractor.extract_from_chat(1, "x", "y")
    assert summary["added"] == 1
