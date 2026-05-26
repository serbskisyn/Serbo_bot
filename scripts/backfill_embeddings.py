"""
backfill_embeddings.py — Compute embeddings for existing open todos + people.

Run once after upgrading to Phase 5 so the semantic dedup has data to
match against. Idempotent — re-running just overwrites existing rows.

Usage:
    python -m scripts.backfill_embeddings
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def backfill_todos() -> int:
    from app.services import semantic, todos as todos_svc
    import aiosqlite

    await todos_svc.init_db()
    count = 0
    async with aiosqlite.connect(todos_svc.TODOS_DB) as db:
        async with db.execute(
            "SELECT id, user_id, text FROM todos WHERE status = 'open'"
        ) as cur:
            rows = await cur.fetchall()
    for tid, uid, text in rows:
        ok = await semantic.store("todos", int(tid), int(uid), text or "")
        if ok:
            count += 1
            print(f"  todos #{tid:>4} ({uid}) — embedded")
    return count


async def backfill_people() -> int:
    from app.bot import profile
    from app.services import semantic

    profile._load()  # ensure store fresh from disk
    count = 0
    for uid_str, payload in (profile._store or {}).items():
        try:
            uid = int(uid_str)
        except ValueError:
            continue
        people = (payload or {}).get("people") or []
        for idx, p in enumerate(people):
            if not isinstance(p, dict):
                continue
            name = (p.get("name") or "").strip()
            if not name:
                continue
            ref_id = -(idx + 1)
            ok = await semantic.store("people", ref_id, uid, name)
            if ok:
                count += 1
                print(f"  people #{ref_id:>3} ({uid}) — '{name}' embedded")
    return count


async def main() -> int:
    print("📥 Backfill: todos.open → semantic.todos")
    n_todos = await backfill_todos()
    print(f"   ✓ {n_todos} todos embedded\n")

    print("📥 Backfill: profile.people → semantic.people")
    n_people = await backfill_people()
    print(f"   ✓ {n_people} people embedded\n")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
