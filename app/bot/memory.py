"""
memory.py — Backwards-compat shim around the structured profile module.

The legacy flat-dict memory.json has been replaced by app/bot/profile.py,
which uses a hierarchical YAML profile + a 3-stage learning pipeline
(see app/services/profile_learner.py).

This module re-exports the original API (add_direct, add_indirect,
get_confirmed, get_memory_prompt, clear_memory, format_memory_overview)
so older callers keep working without code changes.

New code should import directly from `app.bot.profile` and use
the structured API (set_scalar / append_list / add_dict_item / apply_ops).
"""
from __future__ import annotations

import asyncio
import logging

from app.bot import profile

logger = logging.getLogger(__name__)

# Legacy module-level attributes kept for backwards compat
INDIRECT_THRESHOLD = profile.PENDING_PROMOTION_MENTIONS  # legacy default = 5; new = 3
MEMORY_FILE = profile.PROFILE_FILE
_lock = profile._lock
_store = profile._store


# Heuristic mapping from old flat keys to structured sections.
_IDENTITY_KEYS = {"name", "wohnort", "ort", "stadt", "location",
                  "alter", "age", "geburtsort", "birthplace", "role", "rolle"}
_WORK_KEYS = {"firma", "company", "team", "industry", "branche", "position"}
_INTEREST_KEYS = {
    "interesse", "interessen",
    "lieblingsverein", "lieblingsvereine", "lieblingsclub", "lieblingsmannschaft",
    "vereine", "club", "clubs", "hobby", "hobbies", "thema",
}


def _section_for_key(key: str) -> tuple[str, str]:
    """Map a legacy flat key to (section, normalized_key).

    Returns (section, key) where section ∈ {identity, work, interests, facts}.
    interests is special — value goes into the list, key is ignored.
    """
    k = key.strip().lower()
    if k in _IDENTITY_KEYS or k.startswith(("name", "wohnort", "alter", "geburts")):
        return "identity", k
    if k in _WORK_KEYS:
        return "work", k
    if k in _INTEREST_KEYS or k.startswith(("interesse", "hobby", "thema")):
        return "interests", k
    return "facts", k


# ── Legacy API ───────────────────────────────────────────────────────────────


async def add_direct(user_id: int, key: str, value: str) -> None:
    """Legacy: store a fact directly (no LLM filtering)."""
    section, norm_key = _section_for_key(key)
    if section == "interests":
        await profile.append_list(user_id, "interests", str(value))
    elif section == "identity":
        await profile.set_scalar(user_id, "identity", norm_key, value)
    elif section == "work":
        await profile.set_scalar(user_id, "work", norm_key, value)
    else:
        await profile.add_fact(user_id, norm_key, value)


async def add_indirect(user_id: int, fact: str) -> None:
    """Legacy: stash a candidate. Promotes after N mentions for backwards-compat."""
    await profile.add_pending(user_id, fact, fact_type="legacy", confidence=0.4)
    # Auto-promote when the legacy threshold is hit
    user = profile._get_user(user_id)
    norm = fact.strip().lower()
    for cand in user.get("pending", []):
        if cand.get("text", "").lower() == norm and cand.get("mentions", 0) >= INDIRECT_THRESHOLD:
            key, _, value = fact.partition(":")
            key = key.strip().lower() or norm
            value = value.strip() or fact.strip()
            section, norm_key = _section_for_key(key)
            if section == "interests":
                await profile.append_list(user_id, "interests", value)
            elif section in ("identity", "work"):
                await profile.set_scalar(user_id, section, norm_key, value)
            else:
                await profile.add_fact(user_id, norm_key, value)
            # Remove the now-promoted candidate
            async with profile._lock:
                user = profile._get_user(user_id)
                user["pending"] = [
                    c for c in user.get("pending", [])
                    if c.get("text", "").lower() != norm
                ]
                profile._save(profile._store)
            break


def get_confirmed(user_id: int) -> dict:
    """Legacy: flat key-value view of confirmed facts."""
    return profile.as_flat_confirmed(user_id)


def get_memory_prompt(user_id: int) -> str:
    """Legacy: German prompt-context block."""
    return profile.as_prompt(user_id)


async def clear_memory(user_id: int) -> None:
    """Legacy: wipe the user's profile."""
    await profile.clear(user_id)


def format_memory_overview(user_id: int) -> str:
    """Legacy: Markdown overview for the /memory command."""
    return profile.as_overview(user_id)
