"""
profile.py — Structured per-user profile with YAML persistence.

Replaces the flat key/value memory.json with a hierarchical model:

  identity   — name, location, age, birthplace, role
  work       — company, team, industry
  interests  — list of free-form interests/topics
  preferences — communication style etc.
  people     — known contacts (filled by Granola pipeline in Phase 3)
  projects   — ongoing projects (Phase 3)
  goals      — user-stated objectives
  facts      — free-form catch-all key/value bucket
  pending    — candidate facts not yet promoted (confidence-scored)
  meta       — timestamps

memory.py is kept as a backwards-compat shim that maps the legacy
add_direct/add_indirect/get_confirmed/... API onto this module.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

PROFILE_FILE = Path(__file__).parent.parent / "data" / "profile.yaml"

# Sections that hold structured single-value fields
_SCALAR_SECTIONS = ("identity", "work", "preferences")
# Sections that hold lists of strings
_LIST_SECTIONS = ("interests",)
# Sections that hold lists of dicts
_DICT_LIST_SECTIONS = ("people", "projects", "goals")

# Promotion threshold for pending candidates (kept for legacy reasons,
# but the new 3-stage learner usually writes directly to confirmed).
PENDING_PROMOTION_CONFIDENCE = 0.75
PENDING_PROMOTION_MENTIONS = 3

_lock = asyncio.Lock()


def _empty_profile() -> dict:
    return {
        "identity": {},
        "work": {},
        "interests": [],
        "preferences": {},
        "people": [],
        "projects": [],
        "goals": [],
        "facts": {},
        "pending": [],
        "meta": {},
    }


def _load() -> dict:
    if not PROFILE_FILE.exists():
        return {}
    try:
        with PROFILE_FILE.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                logger.warning("profile.yaml has unexpected root type; resetting")
                return {}
            return data
    except Exception as exc:
        logger.warning(f"Profile-Datei konnte nicht geladen werden: {exc}")
        return {}


def _save(store: dict) -> None:
    try:
        PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PROFILE_FILE.with_suffix(".yaml.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            yaml.safe_dump(store, f, allow_unicode=True, sort_keys=False, indent=2)
        tmp.replace(PROFILE_FILE)
    except Exception as exc:
        logger.warning(f"Profile-Datei konnte nicht gespeichert werden: {exc}")


_store: dict = _load()


def _get_user(user_id: int) -> dict:
    key = str(user_id)
    if key not in _store:
        _store[key] = _empty_profile()
    user = _store[key]
    for section, default in _empty_profile().items():
        user.setdefault(section, default)
    return user


def _now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Structured read ──────────────────────────────────────────────────────────


def get_profile(user_id: int) -> dict:
    """Return a deep copy of the user's full profile."""
    import copy
    return copy.deepcopy(_get_user(user_id))


def get_section(user_id: int, section: str) -> Any:
    user = _get_user(user_id)
    return user.get(section)


# ── Structured write ─────────────────────────────────────────────────────────


async def set_scalar(user_id: int, section: str, key: str, value: Any) -> None:
    """Set identity.name = 'Benno', work.company = 'Atolls' etc."""
    async with _lock:
        user = _get_user(user_id)
        if section not in _SCALAR_SECTIONS:
            user.setdefault("facts", {})[key] = value
        else:
            user.setdefault(section, {})[key] = value
        user.setdefault("meta", {})["updated_at"] = _now()
        _save(_store)


async def append_list(user_id: int, section: str, value: str) -> None:
    """Append to interests, etc. — deduplicates case-insensitively."""
    async with _lock:
        user = _get_user(user_id)
        bucket = user.setdefault(section, [])
        if not isinstance(bucket, list):
            return
        norm = value.strip()
        if not norm:
            return
        existing = {str(v).strip().lower() for v in bucket if isinstance(v, str)}
        if norm.lower() not in existing:
            bucket.append(norm)
            user.setdefault("meta", {})["updated_at"] = _now()
            _save(_store)


async def add_dict_item(user_id: int, section: str, item: dict) -> None:
    """Append a structured dict to people/projects/goals.

    Deduplicates on the `name` or `text` key.
    """
    async with _lock:
        user = _get_user(user_id)
        bucket = user.setdefault(section, [])
        if not isinstance(bucket, list):
            return
        ident = (item.get("name") or item.get("text") or "").strip().lower()
        if not ident:
            return
        for existing in bucket:
            ex_ident = (existing.get("name") or existing.get("text") or "").strip().lower()
            if ex_ident == ident:
                existing.update(item)
                user.setdefault("meta", {})["updated_at"] = _now()
                _save(_store)
                return
        bucket.append(item)
        user.setdefault("meta", {})["updated_at"] = _now()
        _save(_store)


async def add_fact(user_id: int, key: str, value: Any) -> None:
    """Set a free-form fact in the catch-all bucket."""
    async with _lock:
        user = _get_user(user_id)
        user.setdefault("facts", {})[key.strip().lower()] = value
        user.setdefault("meta", {})["updated_at"] = _now()
        _save(_store)


async def add_pending(user_id: int, text: str, fact_type: str = "unknown",
                      confidence: float = 0.3) -> None:
    """Track a low-confidence candidate fact.

    Repeated calls increase mention_count. When confidence × mentions
    crosses thresholds, callers can promote it via promote_pending().
    """
    async with _lock:
        user = _get_user(user_id)
        norm = text.strip().lower()
        if not norm:
            return
        for cand in user.setdefault("pending", []):
            if cand.get("text", "").lower() == norm:
                cand["mentions"] = int(cand.get("mentions", 0)) + 1
                cand["last_seen"] = _now()
                cand["confidence"] = max(float(cand.get("confidence", 0)), confidence)
                user.setdefault("meta", {})["updated_at"] = _now()
                _save(_store)
                return
        user["pending"].append({
            "text": text.strip(),
            "type": fact_type,
            "confidence": confidence,
            "mentions": 1,
            "first_seen": _now(),
            "last_seen": _now(),
        })
        user.setdefault("meta", {})["updated_at"] = _now()
        _save(_store)


async def promote_pending(user_id: int, text: str, section: str, key: str | None = None) -> bool:
    """Move a pending candidate into a confirmed section. Returns True on success."""
    async with _lock:
        user = _get_user(user_id)
        norm = text.strip().lower()
        for i, cand in enumerate(user.get("pending", [])):
            if cand.get("text", "").lower() == norm:
                value = cand["text"]
                if section in _SCALAR_SECTIONS:
                    if not key:
                        return False
                    user.setdefault(section, {})[key] = value
                elif section in _LIST_SECTIONS:
                    bucket = user.setdefault(section, [])
                    if value.lower() not in {str(x).lower() for x in bucket}:
                        bucket.append(value)
                else:
                    user.setdefault("facts", {})[key or norm] = value
                user["pending"].pop(i)
                user.setdefault("meta", {})["updated_at"] = _now()
                _save(_store)
                return True
        return False


async def clear(user_id: int) -> None:
    async with _lock:
        _store[str(user_id)] = _empty_profile()
        _save(_store)


# ── Apply structured ops (used by the 3-stage learner) ───────────────────────


async def apply_ops(user_id: int, ops: list[dict]) -> dict:
    """Apply a list of structured update operations.

    Each op: {section, op, key?, value, reason?}
      op ∈ {set, append, add_pending, remove}

    Returns counts: {"applied": N, "skipped": N}.
    """
    applied = 0
    skipped = 0
    for op in ops:
        section = (op.get("section") or "").lower()
        action = (op.get("op") or "").lower()
        key = op.get("key")
        value = op.get("value")
        if not section or not action or value in (None, ""):
            skipped += 1
            continue
        try:
            if action == "set":
                await set_scalar(user_id, section, str(key or ""), value)
            elif action == "append":
                if isinstance(value, list):
                    for v in value:
                        await append_list(user_id, section, str(v))
                else:
                    await append_list(user_id, section, str(value))
            elif action == "add_dict":
                if isinstance(value, dict):
                    await add_dict_item(user_id, section, value)
                else:
                    skipped += 1
                    continue
            elif action == "add_pending":
                await add_pending(
                    user_id,
                    str(value),
                    fact_type=str(op.get("type", "unknown")),
                    confidence=float(op.get("confidence", 0.3)),
                )
            else:
                skipped += 1
                continue
            applied += 1
        except Exception as exc:
            logger.warning(f"apply_ops: {action} on {section}/{key} failed: {exc}")
            skipped += 1
    return {"applied": applied, "skipped": skipped}


# ── Prompt rendering & overview ──────────────────────────────────────────────


def as_prompt(user_id: int) -> str:
    """Render the profile as a German prompt-context block (for general agent)."""
    user = _get_user(user_id)
    lines: list[str] = []

    identity = user.get("identity") or {}
    if identity:
        parts = [f"{k}: {v}" for k, v in identity.items() if v]
        if parts:
            lines.append("Identität — " + ", ".join(parts))

    work = user.get("work") or {}
    if work:
        parts = [f"{k}: {v}" for k, v in work.items() if v]
        if parts:
            lines.append("Arbeit — " + ", ".join(parts))

    interests = user.get("interests") or []
    if interests:
        lines.append("Interessen: " + ", ".join(str(i) for i in interests if i))

    prefs = user.get("preferences") or {}
    if prefs:
        parts = [f"{k}: {v}" for k, v in prefs.items() if v]
        if parts:
            lines.append("Präferenzen — " + ", ".join(parts))

    people = user.get("people") or []
    if people:
        names = [p.get("name", "") for p in people if isinstance(p, dict)]
        names = [n for n in names if n]
        if names:
            lines.append("Bekannte Personen: " + ", ".join(names[:10]))

    facts = user.get("facts") or {}
    if facts:
        parts = []
        for k, v in facts.items():
            if isinstance(v, list):
                parts.append(f"{k}: {', '.join(str(x) for x in v)}")
            elif v:
                parts.append(f"{k}: {v}")
        if parts:
            lines.append("Weitere Fakten — " + " · ".join(parts))

    if not lines:
        return ""
    return "\nWas du über den User weißt:\n" + "\n".join(f"- {l}" for l in lines)


def as_flat_confirmed(user_id: int) -> dict:
    """Flatten the structured profile into a key/value dict.

    Provides the same shape as the legacy `get_confirmed()` API so older
    callers (football_news_agent, session_summary, …) keep working.
    """
    user = _get_user(user_id)
    flat: dict[str, Any] = {}

    for section in _SCALAR_SECTIONS:
        for k, v in (user.get(section) or {}).items():
            if v not in ("", None):
                flat[k.lower()] = v

    facts = user.get("facts") or {}
    for k, v in facts.items():
        flat[k.lower()] = v

    interests = user.get("interests") or []
    if interests:
        joined = ", ".join(str(i) for i in interests if i)
        # Legacy aliases so older agents (football_news, etc.) keep finding clubs
        flat["interessen"] = joined
        flat["lieblingsvereine"] = joined
        flat["lieblingsverein"] = joined

    return flat


def as_overview(user_id: int) -> str:
    """Human-readable Markdown overview for the /memory command."""
    user = _get_user(user_id)

    if not any(user.get(s) for s in _empty_profile()):
        return "Ich habe noch nichts über dich gespeichert."

    lines = ["📋 *Was ich über dich weiß:*\n"]

    identity = user.get("identity") or {}
    if any(identity.values()):
        lines.append("👤 *Identität:*")
        for k, v in identity.items():
            if v:
                lines.append(f"  • {k}: {v}")

    work = user.get("work") or {}
    if any(work.values()):
        lines.append("\n💼 *Arbeit:*")
        for k, v in work.items():
            if v:
                lines.append(f"  • {k}: {v}")

    interests = user.get("interests") or []
    if interests:
        lines.append("\n⭐ *Interessen:*")
        for i in interests:
            lines.append(f"  • {i}")

    prefs = user.get("preferences") or {}
    if any(prefs.values()):
        lines.append("\n🎛 *Präferenzen:*")
        for k, v in prefs.items():
            if v:
                lines.append(f"  • {k}: {v}")

    people = user.get("people") or []
    if people:
        lines.append("\n🤝 *Personen:*")
        for p in people:
            if isinstance(p, dict) and p.get("name"):
                relation = p.get("relation", "")
                lines.append(f"  • {p['name']}" + (f" ({relation})" if relation else ""))

    facts = user.get("facts") or {}
    if facts:
        lines.append("\n🧠 *Weitere Fakten:*")
        for k, v in facts.items():
            if isinstance(v, list):
                lines.append(f"  • {k}: {', '.join(str(x) for x in v)}")
            elif v:
                lines.append(f"  • {k}: {v}")

    pending = user.get("pending") or []
    if pending:
        lines.append("\n⏳ *Noch unbestätigt:*")
        for p in pending[:10]:
            text = p.get("text", "")
            conf = p.get("confidence", 0)
            mentions = p.get("mentions", 0)
            lines.append(f"  • {text} (conf={conf:.2f}, {mentions}x)")

    return "\n".join(lines)
