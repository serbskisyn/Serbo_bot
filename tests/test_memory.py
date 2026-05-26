"""
Tests for the backwards-compat memory shim and the structured profile module.
"""
import asyncio

import pytest
from app.bot import memory as mem_module
from app.bot import profile as profile_module


# ── Legacy add_direct API ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_add_direct_stores_identity():
    await mem_module.add_direct(1, "Name", "Benno")
    assert mem_module.get_confirmed(1)["name"] == "Benno"


@pytest.mark.anyio
async def test_add_direct_lowercases_key():
    await mem_module.add_direct(1, "LIEBLINGSVEREIN", "BVB")
    confirmed = mem_module.get_confirmed(1)
    # Lieblingsverein routes to interests list, which the flat view exposes
    # under the legacy "interessen" key.
    assert "BVB" in confirmed.get("interessen", "")


@pytest.mark.anyio
async def test_add_direct_overwrites_existing_identity():
    await mem_module.add_direct(1, "name", "Alt")
    await mem_module.add_direct(1, "name", "Neu")
    assert mem_module.get_confirmed(1)["name"] == "Neu"


@pytest.mark.anyio
async def test_add_direct_falls_back_to_facts_for_unknown_key():
    await mem_module.add_direct(1, "lieblingsfarbe", "rot")
    assert mem_module.get_confirmed(1)["lieblingsfarbe"] == "rot"


# ── Legacy add_indirect API ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_add_indirect_below_threshold_stays_pending():
    for _ in range(mem_module.INDIRECT_THRESHOLD - 1):
        await mem_module.add_indirect(1, "lieblingsverein: bvb")
    user = profile_module._get_user(1)
    pending_texts = [p["text"].lower() for p in user["pending"]]
    assert "lieblingsverein: bvb" in pending_texts


@pytest.mark.anyio
async def test_add_indirect_promotes_at_threshold():
    for _ in range(mem_module.INDIRECT_THRESHOLD):
        await mem_module.add_indirect(1, "lieblingsverein: bvb")
    flat = mem_module.get_confirmed(1)
    # After promotion the "bvb" value is appended to the interests list,
    # which the flat view exposes via the "interessen" key.
    assert "bvb" in flat.get("interessen", "").lower()


@pytest.mark.anyio
async def test_add_indirect_no_colon_no_crash():
    for _ in range(mem_module.INDIRECT_THRESHOLD):
        await mem_module.add_indirect(1, "kein doppelpunkt vorhanden")
    # Should not have crashed — value lands in facts as catch-all
    flat = mem_module.get_confirmed(1)
    assert "kein doppelpunkt vorhanden" in flat


# ── get_memory_prompt ────────────────────────────────────────────────────────


def test_get_memory_prompt_empty_user():
    assert mem_module.get_memory_prompt(99) == ""


@pytest.mark.anyio
async def test_get_memory_prompt_with_facts():
    await mem_module.add_direct(1, "name", "Benno")
    prompt = mem_module.get_memory_prompt(1)
    assert "Benno" in prompt


# ── clear_memory ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_clear_memory_resets_everything():
    await mem_module.add_direct(1, "name", "Benno")
    await mem_module.add_indirect(1, "wohnort: berlin")
    await mem_module.clear_memory(1)
    assert mem_module.get_confirmed(1) == {}
    assert profile_module._store["1"]["pending"] == []


# ── Isolation ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_different_users_are_isolated():
    await mem_module.add_direct(1, "name", "Benno")
    await mem_module.add_direct(2, "name", "Klaus")
    assert mem_module.get_confirmed(1)["name"] == "Benno"
    assert mem_module.get_confirmed(2)["name"] == "Klaus"


# ── Lock behaviour ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_concurrent_add_direct_no_lost_updates():
    """50 parallel writes to different keys should all land."""
    await asyncio.gather(*[
        mem_module.add_direct(1, f"key{i}", f"val{i}") for i in range(50)
    ])
    confirmed = mem_module.get_confirmed(1)
    assert all(confirmed.get(f"key{i}") == f"val{i}" for i in range(50))


# ── Structured profile API ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_profile_set_scalar():
    await profile_module.set_scalar(1, "identity", "name", "Benno")
    assert profile_module.get_section(1, "identity")["name"] == "Benno"


@pytest.mark.anyio
async def test_profile_append_list_dedupes():
    await profile_module.append_list(1, "interests", "Dortmund")
    await profile_module.append_list(1, "interests", "dortmund")  # case-insensitive
    interests = profile_module.get_section(1, "interests")
    assert len(interests) == 1


@pytest.mark.anyio
async def test_profile_add_dict_item_dedupes_by_name():
    await profile_module.add_dict_item(1, "people", {"name": "Andi", "relation": "friend"})
    await profile_module.add_dict_item(1, "people", {"name": "Andi", "notes": "Kollege"})
    people = profile_module.get_section(1, "people")
    assert len(people) == 1
    assert people[0]["relation"] == "friend"
    assert people[0]["notes"] == "Kollege"


@pytest.mark.anyio
async def test_profile_apply_ops():
    ops = [
        {"section": "identity", "op": "set", "key": "name", "value": "Benno"},
        {"section": "interests", "op": "append", "value": "Astronomie"},
        {"section": "interests", "op": "append", "value": ["Fußball", "Tech"]},
        {"section": "facts", "op": "set", "key": "lieblingsfarbe", "value": "rot"},
        {"section": "identity", "op": "set", "key": "", "value": ""},  # should be skipped
    ]
    result = await profile_module.apply_ops(1, ops)
    assert result["applied"] >= 4
    assert profile_module.get_section(1, "identity")["name"] == "Benno"
    assert set(profile_module.get_section(1, "interests")) >= {"Astronomie", "Fußball", "Tech"}
    assert profile_module.get_section(1, "facts")["lieblingsfarbe"] == "rot"
