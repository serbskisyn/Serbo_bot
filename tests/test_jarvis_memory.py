"""
Tests for the Jarvis-memory layer (curator + soft context + proactive +
notes chunking). LLM/embedding calls are not exercised — only the
deterministic store/merge/score logic.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.bot import profile
from app.services import curator, context_store, proactive_context, notes_index, todos


@pytest.fixture
def tmp_dbs(tmp_path, monkeypatch):
    monkeypatch.setattr(curator, "_STATE_FILE", tmp_path / "curator_state.json")
    monkeypatch.setattr(context_store, "CONTEXT_DB", tmp_path / "context.db")
    monkeypatch.setattr(todos, "TODOS_DB", tmp_path / "todos.db")
    return tmp_path


# ── Curator ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_curator_apply_archives_and_respects_pinned(tmp_dbs):
    uid = 42
    await profile.add_dict_item(uid, "projects", {"name": "Jarvis Memory", "status": "wip"})
    await profile.add_dict_item(uid, "projects", {"name": "Context Layer", "notes": "merge"})
    await profile.add_dict_item(uid, "people", {"name": "Chef", "_pinned": True})
    await profile.append_list(uid, "interests", "Fußball")
    await profile.append_list(uid, "interests", "soccer")
    await profile.add_fact(uid, "altes_projekt", "done")

    base = profile.profile_hash(uid)
    full = profile.get_profile(uid)
    analysis = {
        "dict_duplicates": [{
            "section": "projects", "indices": [0, 1], "keep_index": 0,
            "merged_entry": {"name": "Jarvis Memory", "status": "wip", "notes": "merge"},
            "reason": "same",
        }],
        "interest_duplicates": [{"keep": "Fußball", "remove": ["soccer"], "reason": "syn"}],
        "stale_facts": [{"key": "altes_projekt", "reason": "done"}],
    }
    proposal = curator._sanitize(analysis, full)
    exp = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = curator._load_state()
    state.setdefault("pending", {})[str(uid)] = {
        "proposal": proposal, "base_hash": base, "created_at": exp, "expires_at": exp,
    }
    curator._save_state(state)

    ok, _msg = await curator.apply_pending(uid)
    assert ok
    projects = profile.get_section(uid, "projects")
    assert len(projects) == 1 and projects[0]["notes"] == "merge"
    assert [str(i).lower() for i in profile.get_section(uid, "interests")] == ["fußball"]
    assert "altes_projekt" not in profile.get_section(uid, "facts")
    # pinned survives, archive captured the removed entries
    assert any(p.get("name") == "Chef" for p in profile.get_section(uid, "people"))
    assert len(profile.get_section(uid, "archived")) == 3


@pytest.mark.anyio
async def test_curator_stale_hash_refuses_apply(tmp_dbs):
    uid = 43
    await profile.append_list(uid, "interests", "a")
    await profile.append_list(uid, "interests", "b")
    base = profile.profile_hash(uid)
    proposal = {"dict_duplicates": [], "interest_duplicates": [
        {"keep": "a", "remove": ["b"], "reason": "x"}], "stale_facts": []}
    exp = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = curator._load_state()
    state.setdefault("pending", {})[str(uid)] = {
        "proposal": proposal, "base_hash": base, "created_at": exp, "expires_at": exp}
    curator._save_state(state)
    # mutate the profile so the stored base_hash is now stale
    await profile.append_list(uid, "interests", "c")
    ok, msg = await curator.apply_pending(uid)
    assert not ok and "geändert" in msg


@pytest.mark.anyio
async def test_curator_pinned_indices_dropped_by_sanitize(tmp_dbs):
    uid = 44
    await profile.add_dict_item(uid, "people", {"name": "A"})
    await profile.add_dict_item(uid, "people", {"name": "B", "_pinned": True})
    full = profile.get_profile(uid)
    analysis = {"dict_duplicates": [
        {"section": "people", "indices": [0, 1], "keep_index": 0, "reason": "x"}]}
    proposal = curator._sanitize(analysis, full)
    assert proposal["dict_duplicates"] == []  # pinned in indices → dropped


# ── Context store ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_context_store_prefix_dedup_and_graph(tmp_dbs):
    uid = 1
    a = await context_store.upsert_item(uid, "Martin", entity_type="person")
    b = await context_store.upsert_item(uid, "Martin Gospodinov", entity_type="person")
    assert a == b  # prefix dedup
    proj = await context_store.upsert_item(uid, "Clicky Migration", entity_type="event")
    await context_store.add_link(uid, b, proj)
    await context_store.add_link(uid, b, proj)
    rel = await context_store.get_related(uid, "Martin")
    assert rel and rel[0]["name"] == "Clicky Migration" and rel[0]["weight"] == 2
    names = [p["name"] for p in await context_store.get_pending_items(uid)]
    assert "Martin Gospodinov" in names and "Martin" not in names


@pytest.mark.anyio
async def test_context_pending_priority_orders_due_first(tmp_dbs):
    uid = 2
    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    await context_store.upsert_item(uid, "irgendein Kontakt", entity_type="person")
    await context_store.upsert_item(uid, "Deck abgeben", kind="intent",
                                    entity_type="intent", due_date=tomorrow)
    items = await context_store.get_pending_items(uid)
    assert items[0]["name"] == "Deck abgeben"


# ── Proactive context ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_proactive_dedups_todo_against_soft(tmp_dbs):
    uid = 3
    today = datetime.now(timezone.utc).date().isoformat()
    await todos.add_todo(uid, "MBR Deck finalisieren", source="chat", due_date=today)
    await context_store.upsert_item(uid, "Deck vorbereiten", kind="intent", entity_type="intent")
    await context_store.upsert_item(uid, "Clicky Migration", entity_type="event")
    block = await proactive_context.get_proactive_context(uid)
    assert "MBR Deck finalisieren" in block
    assert "Clicky Migration" in block
    assert block.count("Deck") == 1  # soft 'Deck' deduped against the todo


# ── Notes index ─────────────────────────────────────────────────────────────────


def test_notes_chunking_and_ref_id_stable():
    text = "# Titel\n\n" + ("Absatz eins. " * 40) + "\n\n" + ("Absatz zwei. " * 40)
    chunks = notes_index._chunk(text)
    assert len(chunks) >= 2
    assert all(len(c) <= notes_index._CHUNK_MAX_CHARS + 20 for c in chunks)
    # ref_id deterministic + collision-free across indices
    assert notes_index._ref_id("355857037_2026-06-02.md", 0) == \
           notes_index._ref_id("355857037_2026-06-02.md", 0)
    assert notes_index._ref_id("x.md", 0) != notes_index._ref_id("x.md", 1)


def test_notes_parse_filename():
    assert notes_index._parse_filename(
        __import__("pathlib").Path("355857037_2026-06-02_reflection.md")) == (355857037, "2026-06-02")
    assert notes_index._parse_filename(__import__("pathlib").Path("garbage.md")) is None
