"""
migrate_memory_to_profile.py — One-shot migration from memory.json → profile.yaml.

Reads app/data/memory.json (legacy flat key/value store) and produces
app/data/profile.yaml in the new structured schema.

Mapping rules:
  • name, age, location → identity
  • company, team       → work
  • lieblingsverein(e), interests-like keys → interests (list, deduped)
  • everything else     → facts (free-form bucket)
  • the noisy `pending` dict is discarded — the new 3-stage learner
    replaces the old "5x mention promotion" heuristic.

Run with:
    python -m scripts.migrate_memory_to_profile
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

MEMORY_FILE = REPO_ROOT / "app" / "data" / "memory.json"
PROFILE_FILE = REPO_ROOT / "app" / "data" / "profile.yaml"


_IDENTITY_KEYS = {
    "name": "name",
    "wohnort": "location",
    "ort": "location",
    "stadt": "location",
    "location": "location",
    "alter": "age",
    "age": "age",
    "geburtsort": "birthplace",
    "birthplace": "birthplace",
    "role": "role",
    "rolle": "role",
}

_WORK_KEYS = {
    "firma": "company",
    "company": "company",
    "team": "team",
    "industry": "industry",
    "branche": "industry",
    "position": "position",
}


def _is_interest_key(key: str) -> bool:
    k = key.lower()
    return (
        k.startswith("interesse")
        or k.startswith("lieblings")
        or k.startswith("hobby")
        or k in {"vereine", "club", "clubs"}
    )


def _split_csv(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    parts = str(value).split(",")
    return [p.strip() for p in parts if p.strip()]


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


def migrate_user(confirmed: dict) -> dict:
    p = _empty_profile()
    interests_set: dict[str, None] = {}  # ordered dedupe

    for key, value in confirmed.items():
        k = str(key).lower().strip()
        if not k:
            continue

        if k in _IDENTITY_KEYS:
            p["identity"][_IDENTITY_KEYS[k]] = value
            continue

        if k in _WORK_KEYS:
            p["work"][_WORK_KEYS[k]] = value
            continue

        if _is_interest_key(k):
            for item in _split_csv(value):
                interests_set.setdefault(item, None)
            continue

        p["facts"][k] = value

    p["interests"] = list(interests_set.keys())
    p["meta"] = {
        "migrated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "migrated_from": str(MEMORY_FILE.name),
    }
    return p


def main() -> int:
    if not MEMORY_FILE.exists():
        print(f"⚠️  Keine memory.json gefunden ({MEMORY_FILE}). Nichts zu migrieren.")
        return 0

    try:
        raw = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"❌  memory.json nicht lesbar: {exc}")
        return 1

    if PROFILE_FILE.exists():
        backup = PROFILE_FILE.with_suffix(".yaml.bak")
        PROFILE_FILE.replace(backup)
        print(f"📦  Bestehende profile.yaml gesichert nach {backup.name}")

    migrated: dict[str, dict] = {}
    for user_id, payload in raw.items():
        confirmed = (payload or {}).get("confirmed", {}) or {}
        pending = (payload or {}).get("pending", {}) or {}
        migrated[str(user_id)] = migrate_user(confirmed)
        print(
            f"✓ user={user_id}: {len(confirmed)} confirmed → "
            f"id={len(migrated[user_id]['identity'])} "
            f"work={len(migrated[user_id]['work'])} "
            f"interests={len(migrated[user_id]['interests'])} "
            f"facts={len(migrated[user_id]['facts'])} "
            f"(pending verworfen: {len(pending)})"
        )

    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROFILE_FILE.open("w", encoding="utf-8") as f:
        yaml.safe_dump(migrated, f, allow_unicode=True, sort_keys=False, indent=2)

    legacy_backup = MEMORY_FILE.with_suffix(".json.legacy")
    MEMORY_FILE.replace(legacy_backup)
    print(f"📦  memory.json archiviert als {legacy_backup.name}")

    print(f"✅  Migration fertig: {len(migrated)} User → {PROFILE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
