import asyncio

import pytest
from app.bot import memory as mem_module


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(mem_module, "MEMORY_FILE", tmp_path / "memory.json")


# ── add_direct ────────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_add_direct_stores_fact():
    await mem_module.add_direct(1, "Name", "Benno")
    assert mem_module.get_confirmed(1) == {"name": "Benno"}


@pytest.mark.anyio
async def test_add_direct_lowercases_key():
    await mem_module.add_direct(1, "LIEBLINGSVEREIN", "BVB")
    assert "lieblingsverein" in mem_module.get_confirmed(1)


@pytest.mark.anyio
async def test_add_direct_overwrites_existing():
    await mem_module.add_direct(1, "name", "Alt")
    await mem_module.add_direct(1, "name", "Neu")
    assert mem_module.get_confirmed(1)["name"] == "Neu"


# ── add_indirect ──────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_add_indirect_below_threshold_stays_pending():
    for _ in range(mem_module.INDIRECT_THRESHOLD - 1):
        await mem_module.add_indirect(1, "lieblingsverein: bvb")
    assert "lieblingsverein: bvb" in mem_module._store["1"]["pending"]
    assert "lieblingsverein" not in mem_module.get_confirmed(1)


@pytest.mark.anyio
async def test_add_indirect_promotes_at_threshold():
    for _ in range(mem_module.INDIRECT_THRESHOLD):
        await mem_module.add_indirect(1, "lieblingsverein: bvb")
    confirmed = mem_module.get_confirmed(1)
    assert confirmed.get("lieblingsverein") == "bvb"


@pytest.mark.anyio
async def test_add_indirect_no_colon_no_crash():
    # fact ohne Doppelpunkt: key = ganzer String, value = "" — kein Crash
    for _ in range(mem_module.INDIRECT_THRESHOLD):
        await mem_module.add_indirect(1, "kein doppelpunkt vorhanden")
    confirmed = mem_module.get_confirmed(1)
    assert "kein doppelpunkt vorhanden" in confirmed


# ── get_memory_prompt ─────────────────────────────────────────────────────────
def test_get_memory_prompt_empty_user():
    assert mem_module.get_memory_prompt(99) == ""


@pytest.mark.anyio
async def test_get_memory_prompt_with_facts():
    await mem_module.add_direct(1, "name", "Benno")
    prompt = mem_module.get_memory_prompt(1)
    assert "name" in prompt
    assert "Benno" in prompt


# ── clear_memory ──────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_clear_memory_resets_confirmed_and_pending():
    await mem_module.add_direct(1, "name", "Benno")
    await mem_module.add_indirect(1, "wohnort: berlin")
    await mem_module.clear_memory(1)
    assert mem_module.get_confirmed(1) == {}
    assert mem_module._store["1"]["pending"] == {}


# ── Isolation ─────────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_different_users_are_isolated():
    await mem_module.add_direct(1, "name", "Benno")
    await mem_module.add_direct(2, "name", "Klaus")
    assert mem_module.get_confirmed(1)["name"] == "Benno"
    assert mem_module.get_confirmed(2)["name"] == "Klaus"


# ── Lock-Verhalten ────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_concurrent_add_direct_no_lost_updates():
    """Race-Condition: 50 parallele Writes für verschiedene Keys → alle landen drin."""
    await asyncio.gather(*[
        mem_module.add_direct(1, f"key{i}", f"val{i}") for i in range(50)
    ])
    confirmed = mem_module.get_confirmed(1)
    assert len(confirmed) == 50
    assert all(confirmed[f"key{i}"] == f"val{i}" for i in range(50))
