"""
cleanup_granola_todos.py — Wipe Granola-imported todos + their embeddings, then re-sync.

Use after switching to the user-filtered Granola prompt (Phase 6+ refactor).
Old Granola todos may contain action items belonging to other meeting
participants — the new prompt filters strictly to user-owned items.

Default mode: dry-run. Use --yes to actually delete.
By default re-syncs after wipe; pass --no-resync to skip.

Usage:
    python -m scripts.cleanup_granola_todos                # preview only
    python -m scripts.cleanup_granola_todos --yes          # wipe + resync
    python -m scripts.cleanup_granola_todos --yes --user 355857037
    python -m scripts.cleanup_granola_todos --yes --no-resync
    python -m scripts.cleanup_granola_todos --yes --lookback 72
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def list_granola_todos(user_id: int) -> list[dict]:
    import aiosqlite
    from app.services import todos as todos_svc
    await todos_svc.init_db()
    async with aiosqlite.connect(todos_svc.TODOS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, text, status, due_date, mention_count, notes
               FROM todos
               WHERE user_id = ? AND source = 'granola'
               ORDER BY id""",
            (user_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def wipe_granola_todos(user_id: int, ids: list[int]) -> int:
    """DELETE granola rows + matching semantic embeddings. Returns rows deleted."""
    import aiosqlite
    from app.services import semantic, todos as todos_svc
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    async with aiosqlite.connect(todos_svc.TODOS_DB) as db:
        cur = await db.execute(
            f"DELETE FROM todos WHERE user_id = ? AND id IN ({placeholders})",
            (user_id, *ids),
        )
        await db.commit()
        deleted = cur.rowcount or 0
    # Embedding cleanup — best-effort, per-row
    for tid in ids:
        try:
            await semantic.delete("todos", tid, user_id)
        except Exception as exc:
            logger.debug("semantic delete #%s skipped: %s", tid, exc)
    return deleted


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", type=int, default=355857037, help="Target user_id")
    parser.add_argument("--yes", action="store_true", help="Actually delete (otherwise dry-run)")
    parser.add_argument("--no-resync", action="store_true", help="Skip the Granola re-sync after wipe")
    parser.add_argument("--lookback", type=int, default=48, help="Granola lookback hours for re-sync")
    args = parser.parse_args()

    user_id = args.user

    rows = await list_granola_todos(user_id)
    if not rows:
        print(f"📭  Keine Granola-Todos für user={user_id} gefunden — nichts zu löschen.")
    else:
        print(f"📋  Granola-Todos für user={user_id} ({len(rows)}):\n")
        for r in rows:
            text = (r["text"] or "")[:70]
            status = r.get("status", "?")
            due = r.get("due_date") or "—"
            mentions = r.get("mention_count", 1)
            print(f"  #{r['id']:>3} [{status:8}] {text}  (due={due}, m={mentions})")
        print()

    if not args.yes:
        print("🔒  Dry-run. Verwende --yes um wirklich zu löschen.")
        return 0

    if rows:
        ids = [r["id"] for r in rows]
        deleted = await wipe_granola_todos(user_id, ids)
        print(f"🗑   {deleted} Zeilen gelöscht (todos + Semantic-Embeddings).")
    else:
        print("⏭   Nichts zu löschen.")

    if args.no_resync:
        print("⏭   Re-sync übersprungen (--no-resync).")
        return 0

    print(f"\n📥  Re-sync via granola_sync (lookback={args.lookback}h, user-gefiltert) …")
    from app.services.granola_sync import sync_for_user
    counters = await sync_for_user(user_id, lookback_hours=args.lookback)
    print(f"\n✅  Re-sync fertig:")
    for k, v in counters.items():
        print(f"    {k}: {v}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
