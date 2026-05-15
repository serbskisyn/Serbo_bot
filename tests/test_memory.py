import pytest
from app.bot import memory as mem_module


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(mem_module, "MEMORY_FILE", tmp_path / "memory.json")


# ── add_direct ────────────────────────────────────────────────────────────────
def test_add_direct_stores_fact():
    mem_module.add_direct(1, "Name", "Benno")
    assert mem_module.get_confirmed(1) == {"name": "Benno"}


def test_add_direct_lowercases_key():
    mem_module.add_direct(1, "LIEBLINGSVEREIN", "BVB")
    assert "lieblingsverein" in mem_module.get_confirmed(1)


def test_add_direct_overwrites_existing():
    mem_module.add_direct(1, "name", "Alt")
    mem_module.add_direct(1, "name", "Neu")
    assert mem_module.get_confirmed(1)["name"] == "Neu"


# ── add_indirect ──────────────────────────────────────────────────────────────
def test_add_indirect_below_threshold_stays_pending():
    for _ in range(mem_module.INDIRECT_THRESHOLD - 1):
        mem_module.add_indirect(1, "lieblingsverein: bvb")
    assert "lieblingsverein: bvb" in mem_module._store["1"]["pending"]
    assert "lieblingsverein" not in mem_module.get_confirmed(1)


def test_add_indirect_promotes_at_threshold():
    for _ in range(mem_module.INDIRECT_THRESHOLD):
        mem_module.add_indirect(1, "lieblingsverein: bvb")
    confirmed = mem_module.get_confirmed(1)
    assert confirmed.get("lieblingsverein") == "bvb"


def test_add_indirect_no_colon_no_crash():
    # fact ohne Doppelpunkt: key = ganzer String, value = "" — kein Crash
    for _ in range(mem_module.INDIRECT_THRESHOLD):
        mem_module.add_indirect(1, "kein doppelpunkt vorhanden")
    confirmed = mem_module.get_confirmed(1)
    assert "kein doppelpunkt vorhanden" in confirmed


# ── get_memory_prompt ─────────────────────────────────────────────────────────
def test_get_memory_prompt_empty_user():
    assert mem_module.get_memory_prompt(99) == ""


def test_get_memory_prompt_with_facts():
    mem_module.add_direct(1, "name", "Benno")
    prompt = mem_module.get_memory_prompt(1)
    assert "name" in prompt
    assert "Benno" in prompt


# ── clear_memory ──────────────────────────────────────────────────────────────
def test_clear_memory_resets_confirmed_and_pending():
    mem_module.add_direct(1, "name", "Benno")
    mem_module.add_indirect(1, "wohnort: berlin")
    mem_module.clear_memory(1)
    assert mem_module.get_confirmed(1) == {}
    assert mem_module._store["1"]["pending"] == {}


# ── Isolation ─────────────────────────────────────────────────────────────────
def test_different_users_are_isolated():
    mem_module.add_direct(1, "name", "Benno")
    mem_module.add_direct(2, "name", "Klaus")
    assert mem_module.get_confirmed(1)["name"] == "Benno"
    assert mem_module.get_confirmed(2)["name"] == "Klaus"
