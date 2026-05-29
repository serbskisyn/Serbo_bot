"""Tests for the todo-drop extractor (LLM mocked, semantic store fake-embedded)."""
import math

import pytest

from app.bot import profile
from app.services import (
    embeddings,
    semantic,
    todo_drop_extractor,
    todos as todos_svc,
)


def _fake_embedder():
    async def fake_embed(text: str):
        if not text:
            return None
        vec = [0.0] * embeddings.EMBEDDING_DIM
        for c in text.strip().lower():
            vec[ord(c) % embeddings.EMBEDDING_DIM] += 1.0
        n = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / n for x in vec]
    return fake_embed


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(todos_svc, "TODOS_DB", tmp_path / "todos.db")
    monkeypatch.setattr(semantic, "SEMANTIC_DB", tmp_path / "semantic.db")
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile.yaml")
    monkeypatch.setattr(embeddings, "CACHE_FILE", tmp_path / "embed_cache.bin")
    monkeypatch.setattr(embeddings, "embed", _fake_embedder())
    monkeypatch.setattr(semantic, "embed", _fake_embedder())
    embeddings._cache.clear()
    embeddings._cache_loaded = False
    profile._store.clear()
    yield
    profile._store.clear()


@pytest.mark.anyio
async def test_drop_intent_drops_matching_todo(monkeypatch):
    tid = await todos_svc.add_todo(1, "Zahnbürsten für Hilda und Martha mitbringen")

    async def fake_llm(user_text, assistant_response):
        return '{"drops": ["Zahnbürsten für Hilda und Martha mitbringen"]}'

    monkeypatch.setattr(todo_drop_extractor, "_call_llm", fake_llm)
    summary = await todo_drop_extractor.extract_from_chat(
        1, "Nimm die Zahnbürsten von der Liste", "ok"
    )
    assert summary["dropped"] == 1
    t = await todos_svc.get_todo(1, tid)
    assert t["status"] == "dropped"


@pytest.mark.anyio
async def test_drop_no_match_does_nothing(monkeypatch):
    await todos_svc.add_todo(1, "aaa bbb ccc")

    async def fake_llm(user_text, assistant_response):
        return '{"drops": ["xyz qqq pkj"]}'

    monkeypatch.setattr(todo_drop_extractor, "_call_llm", fake_llm)
    summary = await todo_drop_extractor.extract_from_chat(1, "x", "y")
    assert summary["dropped"] == 0


@pytest.mark.anyio
async def test_drop_skips_too_short(monkeypatch):
    tid = await todos_svc.add_todo(1, "Brot kaufen")

    async def fake_llm(user_text, assistant_response):
        return '{"drops": ["ok", "Brot kaufen"]}'  # "ok" too short, second matches

    monkeypatch.setattr(todo_drop_extractor, "_call_llm", fake_llm)
    summary = await todo_drop_extractor.extract_from_chat(1, "x", "y")
    assert summary["dropped"] == 1


@pytest.mark.anyio
async def test_drop_handles_llm_failure(monkeypatch):
    async def fake_llm(user_text, assistant_response):
        raise RuntimeError("boom")

    monkeypatch.setattr(todo_drop_extractor, "_call_llm", fake_llm)
    summary = await todo_drop_extractor.extract_from_chat(1, "x", "y")
    assert summary == {"detected": 0, "dropped": 0, "ids": []}


@pytest.mark.anyio
async def test_drop_does_not_revive_done_todo(monkeypatch):
    tid = await todos_svc.add_todo(1, "Server reboot")
    await todos_svc.mark_done(1, tid)

    async def fake_llm(user_text, assistant_response):
        return '{"drops": ["Server reboot vergessen"]}'

    monkeypatch.setattr(todo_drop_extractor, "_call_llm", fake_llm)
    summary = await todo_drop_extractor.extract_from_chat(1, "x", "y")
    # Already done → embedding deleted → no semantic match → no drop
    assert summary["dropped"] == 0
