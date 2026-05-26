"""Tests for the sqlite-vec semantic layer + dedup integration."""
import pytest

from app.bot import profile
from app.services import embeddings, semantic, todos as todos_svc


# A pure-Python fake "embedding" function so tests run offline.
#
# Strategy: each input character becomes a one-hot signal in a fixed slot,
# then L2-normalized. Two strings that share most characters end up close
# in vector space — close enough to test the dedup machinery without
# actually hitting OpenAI.
def _fake_embedder(seed_shift: int = 0):
    import math

    async def fake_embed(text: str):
        if not text:
            return None
        text_low = text.strip().lower()
        if not text_low:
            return None
        vec = [0.0] * embeddings.EMBEDDING_DIM
        for c in text_low:
            slot = (ord(c) + seed_shift) % embeddings.EMBEDDING_DIM
            vec[slot] += 1.0
        # L2 normalize
        n = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / n for x in vec]

    return fake_embed


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic, "SEMANTIC_DB", tmp_path / "semantic.db")
    monkeypatch.setattr(todos_svc, "TODOS_DB", tmp_path / "todos.db")
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile.yaml")
    monkeypatch.setattr(embeddings, "CACHE_FILE", tmp_path / "embed_cache.bin")
    monkeypatch.setattr(embeddings, "embed", _fake_embedder())
    monkeypatch.setattr(semantic, "embed", _fake_embedder())
    embeddings._cache.clear()
    embeddings._cache_loaded = False
    profile._store.clear()
    yield
    profile._store.clear()


# ── semantic store basics ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_store_and_find_exact():
    ok = await semantic.store("todos", 1, 1, "Send tracking links to dev team")
    assert ok is True
    hits = await semantic.find_similar(
        "todos", 1, "Send tracking links to dev team",
        threshold=semantic.DIST_STRICT_DEDUP,
    )
    assert len(hits) == 1
    assert hits[0][0] == 1
    assert hits[0][2] < 0.01  # essentially zero distance for identical text


@pytest.mark.anyio
async def test_find_returns_only_user_scope():
    await semantic.store("todos", 1, 1, "Brot kaufen")
    await semantic.store("todos", 2, 2, "Brot kaufen")
    hits_u1 = await semantic.find_similar("todos", 1, "Brot kaufen")
    hits_u2 = await semantic.find_similar("todos", 2, "Brot kaufen")
    assert {h[0] for h in hits_u1} == {1}
    assert {h[0] for h in hits_u2} == {2}


@pytest.mark.anyio
async def test_delete_removes_row():
    await semantic.store("todos", 5, 1, "X")
    await semantic.delete("todos", 5, 1)
    hits = await semantic.find_similar("todos", 1, "X")
    assert hits == []


@pytest.mark.anyio
async def test_unknown_collection_raises():
    with pytest.raises(ValueError):
        await semantic.store("bogus", 1, 1, "X")


# ── todos: semantic mention_existing ─────────────────────────────────────────


@pytest.mark.anyio
async def test_semantic_mention_existing_catches_paraphrase():
    # Add an original — its embedding lands in the vector store
    tid = await todos_svc.add_todo(1, "Send tracking links to dev team")

    # A paraphrase shares almost all the same characters → semantic match
    matched = await todos_svc.mention_existing(1, "Send tracking links to dev team!")
    assert matched == tid

    t = await todos_svc.get_todo(1, tid)
    assert t["mention_count"] == 2


@pytest.mark.anyio
async def test_semantic_mention_ignores_done_todos():
    tid = await todos_svc.add_todo(1, "X")
    await todos_svc.mark_done(1, tid)
    # The embedding should have been deleted on done; even if not, the
    # semantic match must require an *open* todo.
    result = await todos_svc.mention_existing(1, "X")
    assert result is None


@pytest.mark.anyio
async def test_no_semantic_match_for_unrelated_text():
    await todos_svc.add_todo(1, "Brot kaufen")
    matched = await todos_svc.mention_existing(1, "Server-Logs analysieren")
    assert matched is None


# ── profile: semantic people dedup ───────────────────────────────────────────


@pytest.mark.anyio
async def test_people_exact_match_still_works():
    await profile.add_dict_item(1, "people", {"name": "Andi", "relation": "friend"})
    await profile.add_dict_item(1, "people", {"name": "andi", "notes": "Kollege"})
    people = profile.get_section(1, "people")
    assert len(people) == 1
    assert people[0]["relation"] == "friend"
    assert people[0]["notes"] == "Kollege"


@pytest.mark.anyio
async def test_people_semantic_merge_close_names():
    await profile.add_dict_item(1, "people", {"name": "Oliver", "last_mentioned": "2026-05-01"})
    await profile.add_dict_item(1, "people", {"name": "Olive", "last_mentioned": "2026-05-26"})

    people = profile.get_section(1, "people")
    # The fake-embedder treats character-overlap as similarity, so these
    # close-spelled names should collapse to one canonical entry.
    assert len(people) == 1
    # Canonical name preserved
    assert people[0]["name"] == "Oliver"
    # New field merged in
    assert people[0]["last_mentioned"] == "2026-05-26"


@pytest.mark.anyio
async def test_people_distinct_names_stay_separate():
    await profile.add_dict_item(1, "people", {"name": "Andi"})
    await profile.add_dict_item(1, "people", {"name": "Wolfgang"})
    people = profile.get_section(1, "people")
    assert len(people) == 2
