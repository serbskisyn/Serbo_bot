"""
semantic.py — Vector store on sqlite-vec for cross-cutting semantic dedup.

Collections (each = one virtual table):
  • todos      — open-todo texts, scoped by user_id
  • people     — known-person names, scoped by user_id
  • decisions  — meeting decisions from Granola, scoped by user_id

OpenAI text-embedding-3-small returns L2-normalized 1536-dim vectors, so
sqlite-vec's default L2 distance gives us a monotonic proxy for cosine
similarity. Distance thresholds:

  L2 < 0.55  →  cos > 0.85  →  strict dedup ("Ollie" ≈ "Oliver")
  L2 < 0.80  →  cos > 0.68  →  fuzzy match (related but not duplicate)
  L2 < 1.10  →  cos > 0.40  →  loose recall (free-text search)

Sync DB ops are run in a thread executor since aiosqlite doesn't load
extensions consistently across versions and the workload is tiny
(<5ms per query at our scale).
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import struct
from pathlib import Path

import sqlite_vec

from app.services.embeddings import EMBEDDING_DIM, embed

logger = logging.getLogger(__name__)

SEMANTIC_DB = Path(__file__).parent.parent / "data" / "semantic.db"

COLLECTIONS = ("todos", "people", "decisions", "notes", "entities", "intents")

# Distance thresholds — calibrated against gemini-embedding-2 (3072-dim) via the
# LiteLLM proxy. Gemini's L2 distances are compressed into a narrower band than
# OpenAI's, so these are tighter than the old OpenAI values. Reference distances:
#   "Brot kaufen" ↔ "Brot kaufen gehen"          = 0.56  (paraphrase → merge)
#   "Send tracking links" ↔ "...URLs for testing"= 0.49  (paraphrase → merge)
#   "Oliver" ↔ "Ollie"                           = 0.76  (synonym → merge)
#   "Kimberly" ↔ "Kim"                           = 0.66  (synonym → merge)
#   "Brot kaufen" ↔ "Milch kaufen"               = 0.73  (distinct task → keep)
#   "Kim" ↔ "Kevin"                              = 0.83  (distinct → keep)
#   "Oliver" ↔ "Martin"                          = 0.96  (distinct → keep)
DIST_STRICT_DEDUP = 0.65   # todos / general dedup (merge ≤0.56, keep ≥0.73)
DIST_PEOPLE_DEDUP = 0.80   # names (merge ≤0.76, keep ≥0.83)
DIST_FUZZY_MATCH  = 0.70   # completion/drop match — related same task, not distinct
DIST_LOOSE_RECALL = 0.95   # free-text recall search (ranked top-k)


def _conn() -> sqlite3.Connection:
    SEMANTIC_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(SEMANTIC_DB)
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    return con


def _init_sync() -> None:
    with _conn() as con:
        for name in COLLECTIONS:
            con.execute(
                f"""CREATE VIRTUAL TABLE IF NOT EXISTS vec_{name} USING vec0(
                       embedding float[{EMBEDDING_DIM}],
                       +ref_id    INTEGER,
                       +user_id   INTEGER,
                       +text      TEXT
                   )"""
            )


async def init_db() -> None:
    await asyncio.to_thread(_init_sync)


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


# ── Sync helpers (run in executor) ───────────────────────────────────────────


def _store_sync(collection: str, ref_id: int, user_id: int, text: str, vec: list[float]) -> None:
    with _conn() as con:
        # idempotency: remove an old row with the same ref_id+user_id first
        con.execute(
            f"DELETE FROM vec_{collection} WHERE ref_id = ? AND user_id = ?",
            (ref_id, user_id),
        )
        con.execute(
            f"INSERT INTO vec_{collection}(embedding, ref_id, user_id, text) VALUES (?, ?, ?, ?)",
            (_pack(vec), ref_id, user_id, text),
        )


def _find_similar_sync(
    collection: str,
    user_id: int,
    vec: list[float],
    threshold: float,
    limit: int,
) -> list[tuple[int, str, float]]:
    """sqlite-vec KNN scan: over-fetch with k=?, then filter by user_id + threshold.

    sqlite-vec requires the KNN constraint to be either `LIMIT N` or `k = ?`
    and won't accept extra WHERE predicates against auxiliary columns in
    the same query — hence the post-filter.
    """
    over_fetch = max(limit * 8, 32)
    with _conn() as con:
        cur = con.execute(
            f"""SELECT ref_id, user_id, text, distance
               FROM vec_{collection}
               WHERE embedding MATCH ? AND k = ?
               ORDER BY distance""",
            (_pack(vec), over_fetch),
        )
        rows = cur.fetchall()
    out: list[tuple[int, str, float]] = []
    for ref_id, owner_id, text, dist in rows:
        if int(owner_id) != int(user_id):
            continue
        if float(dist) > threshold:
            continue
        out.append((int(ref_id), text or "", float(dist)))
        if len(out) >= limit:
            break
    return out


def _delete_sync(collection: str, ref_id: int, user_id: int) -> None:
    with _conn() as con:
        con.execute(
            f"DELETE FROM vec_{collection} WHERE ref_id = ? AND user_id = ?",
            (ref_id, user_id),
        )


# ── Public async API ─────────────────────────────────────────────────────────


async def store(collection: str, ref_id: int, user_id: int, text: str) -> bool:
    """Embed + insert. Returns True on success, False on embedding failure."""
    if collection not in COLLECTIONS:
        raise ValueError(f"unknown collection: {collection}")
    text = (text or "").strip()
    if not text:
        return False
    vec = await embed(text)
    if vec is None:
        return False
    await init_db()
    await asyncio.to_thread(_store_sync, collection, ref_id, user_id, text, vec)
    return True


async def find_similar(
    collection: str,
    user_id: int,
    text: str,
    threshold: float = DIST_STRICT_DEDUP,
    limit: int = 5,
) -> list[tuple[int, str, float]]:
    """Return [(ref_id, text, distance), ...] sorted closest-first.

    Empty list on embedding failure — callers should treat that as
    "no semantic match found" so they fall through to their own logic.
    """
    if collection not in COLLECTIONS:
        raise ValueError(f"unknown collection: {collection}")
    text = (text or "").strip()
    if not text:
        return []
    vec = await embed(text)
    if vec is None:
        return []
    await init_db()
    return await asyncio.to_thread(_find_similar_sync, collection, user_id, vec, threshold, limit)


async def delete(collection: str, ref_id: int, user_id: int) -> None:
    if collection not in COLLECTIONS:
        raise ValueError(f"unknown collection: {collection}")
    await init_db()
    await asyncio.to_thread(_delete_sync, collection, ref_id, user_id)


async def clear_collection(collection: str, user_id: int) -> int:
    """Delete all vectors in a collection for one user. Returns rows removed."""
    if collection not in COLLECTIONS:
        raise ValueError(f"unknown collection: {collection}")
    await init_db()

    def _clear():
        with _conn() as con:
            cur = con.execute(
                f"DELETE FROM vec_{collection} WHERE user_id = ?", (user_id,)
            )
            return cur.rowcount or 0

    return await asyncio.to_thread(_clear)


async def count(collection: str, user_id: int | None = None) -> int:
    def _q():
        with _conn() as con:
            if user_id is None:
                cur = con.execute(f"SELECT COUNT(*) FROM vec_{collection}")
            else:
                cur = con.execute(f"SELECT COUNT(*) FROM vec_{collection} WHERE user_id = ?", (user_id,))
            return cur.fetchone()[0]
    await init_db()
    return await asyncio.to_thread(_q)
