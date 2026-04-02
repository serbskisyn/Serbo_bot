import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

INDIRECT_THRESHOLD = 5
MEMORY_FILE = Path(__file__).parent.parent / "data" / "memory.json"


# ── Laden beim Start ──────────────────────────────────────────────────────────
def _load() -> dict:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Memory-Datei konnte nicht geladen werden: {e}")
    return {}


def _save(store: dict) -> None:
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Memory-Datei konnte nicht gespeichert werden: {e}")


_store: dict = _load()


def _get_user(user_id: int) -> dict:
    key = str(user_id)
    if key not in _store:
        _store[key] = {"confirmed": {}, "pending": {}}
    return _store[key]


def add_direct(user_id: int, key: str, value: str) -> None:
    user = _get_user(user_id)
    user["confirmed"][key.lower()] = value
    _save(_store)


def add_indirect(user_id: int, fact: str) -> None:
    user = _get_user(user_id)
    fact = fact.lower()
    user["pending"][fact] = user["pending"].get(fact, 0) + 1
    if user["pending"][fact] >= INDIRECT_THRESHOLD:
        key, _, value = fact.partition(":")
        user["confirmed"][key.strip()] = value.strip()
        del user["pending"][fact]
    _save(_store)


def get_confirmed(user_id: int) -> dict:
    return dict(_get_user(user_id)["confirmed"])


def get_memory_prompt(user_id: int) -> str:
    facts = _get_user(user_id)["confirmed"]
    if not facts:
        return ""
    lines = "\n".join(f"- {k}: {v}" for k, v in facts.items())
    return f"\nWas du über den User weißt:\n{lines}"


def clear_memory(user_id: int) -> None:
    _store[str(user_id)] = {"confirmed": {}, "pending": {}}
    _save(_store)


def format_memory_overview(user_id: int) -> str:
    user = _get_user(user_id)
    confirmed = user["confirmed"]
    pending = user["pending"]

    if not confirmed and not pending:
        return "Ich habe noch nichts über dich gespeichert."

    lines = ["📋 *Was ich über dich weiß:*\n"]

    if confirmed:
        lines.append("✅ *Bestätigt:*")
        for k, v in confirmed.items():
            lines.append(f"  • {k}: {v}")

    if pending:
        lines.append("\n⏳ *Noch nicht sicher (indirekt erwähnt):*")
        for fact, count in pending.items():
            lines.append(f"  • {fact} ({count}/{INDIRECT_THRESHOLD})")

    return "\n".join(lines)
