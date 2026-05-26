"""Tests for completion_extractor — LLM detect + semantic match + mark_done."""
import math

import pytest

from app.bot import profile
from app.services import completion_extractor, embeddings, semantic, todos as todos_svc


# Fake embedder (same idea as test_semantic.py) — character-overlap → similarity.
def _fake_embedder():
    async def fake_embed(text: str):
        if not text:
            return None
        text_low = text.strip().lower()
        vec = [0.0] * embeddings.EMBEDDING_DIM
        for c in text_low:
            slot = ord(c) % embeddings.EMBEDDING_DIM
            vec[slot] += 1.0
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
async def test_completion_marks_matching_todo_done(monkeypatch):
    tid = await todos_svc.add_todo(1, "Send tracking links to dev team")

    async def fake_llm(user_text, assistant_response):
        return '{"completions": ["Send tracking links to dev team!"]}'

    monkeypatch.setattr(completion_extractor, "_call_llm", fake_llm)
    summary = await completion_extractor.extract_from_chat(
        1, "habe die tracking links geschickt", "ok"
    )
    assert summary["marked_done"] == 1
    assert summary["ids"][0][0] == tid

    t = await todos_svc.get_todo(1, tid)
    assert t["status"] == "done"


@pytest.mark.anyio
async def test_completion_no_match_does_not_mark(monkeypatch):
    # Disjoint character sets so the character-overlap fake embedder
    # gives a wide distance.
    await todos_svc.add_todo(1, "aaa bbb ccc")

    async def fake_llm(user_text, assistant_response):
        return '{"completions": ["xyz qqq pkj"]}'

    monkeypatch.setattr(completion_extractor, "_call_llm", fake_llm)
    summary = await completion_extractor.extract_from_chat(1, "x", "y")
    assert summary["marked_done"] == 0


@pytest.mark.anyio
async def test_completion_skips_short_phrases(monkeypatch):
    tid = await todos_svc.add_todo(1, "Brot kaufen")

    async def fake_llm(user_text, assistant_response):
        return '{"completions": ["ok", "Brot kaufen"]}'

    monkeypatch.setattr(completion_extractor, "_call_llm", fake_llm)
    summary = await completion_extractor.extract_from_chat(1, "x", "y")
    # "ok" is too short → skipped; "Brot kaufen" matches → 1 marked
    assert summary["marked_done"] == 1


@pytest.mark.anyio
async def test_completion_caps_at_3(monkeypatch):
    distinct = [
        "Brot kaufen", "Steuer einreichen", "Mama anrufen",
        "Geschenk besorgen", "Logs analysieren",
    ]
    for t in distinct:
        await todos_svc.add_todo(1, t)

    async def fake_llm(user_text, assistant_response):
        completions = ", ".join(f'"{t}"' for t in distinct)
        return f'{{"completions": [{completions}]}}'

    monkeypatch.setattr(completion_extractor, "_call_llm", fake_llm)
    summary = await completion_extractor.extract_from_chat(1, "x", "y")
    assert summary["detected"] == 5
    assert summary["marked_done"] <= 3


@pytest.mark.anyio
async def test_completion_handles_llm_failure(monkeypatch):
    async def fake_llm(user_text, assistant_response):
        raise RuntimeError("boom")

    monkeypatch.setattr(completion_extractor, "_call_llm", fake_llm)
    summary = await completion_extractor.extract_from_chat(1, "x", "y")
    assert summary == {"detected": 0, "marked_done": 0, "ids": []}


@pytest.mark.anyio
async def test_completion_does_not_revive_already_done_todo(monkeypatch):
    tid = await todos_svc.add_todo(1, "Server reboot")
    await todos_svc.mark_done(1, tid)

    async def fake_llm(user_text, assistant_response):
        return '{"completions": ["Server reboot durchgeführt"]}'

    monkeypatch.setattr(completion_extractor, "_call_llm", fake_llm)
    summary = await completion_extractor.extract_from_chat(1, "x", "y")
    # Already done → semantic store entry was deleted → no match
    assert summary["marked_done"] == 0
