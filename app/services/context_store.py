"""
context_store.py — soft personal-context layer: entities, intents, links.

This is the "soft" counterpart to the hard todos.db. Where todos.db holds
committed, actionable items with status transitions, this store holds the
looser signal the bot picks up in conversation:

  • entities  — people / places / events / tasks the user mentions
  • intents   — soft commitments ("ich sollte/wollte X") that aren't (yet) todos
  • links     — co-occurrence edges between items → a relationship graph

It deliberately does NOT auto-create todos (the existing todo_extractor owns
that). Its value is the graph + a mention-weighted "pending" view the
proactive-context layer surfaces mid-conversation.

Dedup is exact + whole-token-prefix on the normalized name (so
"Martin" ≈ "Martin Gospodinov" without an embedding call). Each repeat
mention bumps mention_count and refreshes last_mentioned_at.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

CONTEXT_DB = Path(__file__).parent.parent / "data" / "context.db"

KINDS = ("entity", "intent")
ENTITY_TYPES = ("person", "place", "event", "task", "intent")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                 -- entity|intent
    entity_type TEXT NOT NULL,          -- person|place|event|task|intent
    name TEXT NOT NULL,
    context TEXT,
    status TEXT NOT NULL DEFAULT 'open',-- open|done
    due_date TEXT,
    mention_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_mentioned_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ctx_user_status ON context_items(user_id, status);
CREATE INDEX IF NOT EXISTS idx_ctx_user_name ON context_items(user_id, name);

CREATE TABLE IF NOT EXISTS context_links (
    user_id INTEGER NOT NULL,
    a_id INTEGER NOT NULL,              -- always the smaller id
    b_id INTEGER NOT NULL,
    weight INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, a_id, b_id)
);
"""

_TYPE_SCORE = {"task": 20, "event": 18, "intent": 15, "person": 10, "place": 5}


async def init_db() -> None:
    CONTEXT_DB.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(CONTEXT_DB) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def _token_prefix_match(a: str, b: str) -> bool:
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return False
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return longer[: len(shorter)] == shorter


# ── Write ─────────────────────────────────────────────────────────────────────


async def upsert_item(
    user_id: int,
    name: str,
    kind: str = "entity",
    entity_type: str = "person",
    context: str = "",
    due_date: str | None = None,
) -> int | None:
    """Insert or bump a context item. Returns its id (None on empty name).

    Dedup: exact-normalized-name then whole-token-prefix within the same user.
    On a prefix hit the LONGER name becomes canonical (Martin → Martin G.).
    """
    name = (name or "").strip()
    if not name:
        return None
    if kind not in KINDS:
        kind = "entity"
    if entity_type not in ENTITY_TYPES:
        entity_type = "intent" if kind == "intent" else "person"

    await init_db()
    norm = _norm(name)
    now = _now_iso()

    async with aiosqlite.connect(CONTEXT_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name FROM context_items WHERE user_id = ? AND kind = ?",
            (user_id, kind),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        match_id = None
        promote_name = None
        for r in rows:
            ex = _norm(r["name"])
            if ex == norm:
                match_id = r["id"]
                break
            if _token_prefix_match(norm, ex):
                match_id = r["id"]
                if len(name.split()) > len(r["name"].split()):
                    promote_name = name  # incoming is fuller
                break

        if match_id is not None:
            if promote_name:
                await db.execute(
                    """UPDATE context_items
                       SET mention_count = mention_count + 1, last_mentioned_at = ?,
                           name = ?, context = COALESCE(NULLIF(?, ''), context),
                           due_date = COALESCE(?, due_date)
                       WHERE id = ?""",
                    (now, promote_name, context, due_date, match_id),
                )
            else:
                await db.execute(
                    """UPDATE context_items
                       SET mention_count = mention_count + 1, last_mentioned_at = ?,
                           context = COALESCE(NULLIF(?, ''), context),
                           due_date = COALESCE(?, due_date)
                       WHERE id = ?""",
                    (now, context, due_date, match_id),
                )
            await db.commit()
            return match_id

        cur = await db.execute(
            """INSERT INTO context_items
               (user_id, kind, entity_type, name, context, status, due_date,
                mention_count, created_at, last_mentioned_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, 1, ?, ?)""",
            (user_id, kind, entity_type, name, context, due_date, now, now),
        )
        await db.commit()
        return cur.lastrowid


async def add_link(user_id: int, id_a: int, id_b: int) -> None:
    """Add/strengthen an undirected co-occurrence edge between two items."""
    if id_a == id_b:
        return
    a, b = (id_a, id_b) if id_a < id_b else (id_b, id_a)
    await init_db()
    now = _now_iso()
    async with aiosqlite.connect(CONTEXT_DB) as db:
        await db.execute(
            """INSERT INTO context_links (user_id, a_id, b_id, weight, updated_at)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(user_id, a_id, b_id)
               DO UPDATE SET weight = weight + 1, updated_at = excluded.updated_at""",
            (user_id, a, b, now),
        )
        await db.commit()


async def mark_done(user_id: int, name_query: str) -> list[str]:
    """Mark open items whose name contains name_query as done. Returns names."""
    q = _norm(name_query)
    if not q:
        return []
    await init_db()
    async with aiosqlite.connect(CONTEXT_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name FROM context_items WHERE user_id = ? AND status = 'open'",
            (user_id,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        matched = [r for r in rows if q in _norm(r["name"])]
        for r in matched:
            await db.execute(
                "UPDATE context_items SET status = 'done', last_mentioned_at = ? WHERE id = ?",
                (_now_iso(), r["id"]),
            )
        await db.commit()
    return [r["name"] for r in matched]


# ── Read ──────────────────────────────────────────────────────────────────────


def _due_score(due: str | None, today: date) -> int:
    if not due:
        return 0
    try:
        d = datetime.strptime(due[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 0
    days = (d - today).days
    if days < 0:
        return 50
    if days == 0:
        return 48
    if days <= 3:
        return 40
    if days <= 7:
        return 30
    if days <= 30:
        return 20
    return 5


def _is_stale(due: str | None, last_mentioned: str, today: date,
              max_overdue_days: int = 30, idle_days: int = 45) -> bool:
    """Drop items long overdue, or never given a due date and untouched for ages."""
    if due:
        try:
            d = datetime.strptime(due[:10], "%Y-%m-%d").date()
            if (today - d).days > max_overdue_days:
                return True
        except (ValueError, TypeError):
            pass
    try:
        lm = datetime.strptime(last_mentioned[:10], "%Y-%m-%d").date()
        if (today - lm).days > idle_days:
            return True
    except (ValueError, TypeError):
        pass
    return False


def _priority(row: dict, today: date) -> int:
    return (
        _due_score(row.get("due_date"), today)
        + min(int(row.get("mention_count", 1)) * 5, 30)
        + _TYPE_SCORE.get(row.get("entity_type", ""), 0)
    )


async def get_pending_items(user_id: int, limit: int = 8) -> list[dict[str, Any]]:
    """Open items sorted by priority, stale ones dropped. For proactive context."""
    await init_db()
    today = datetime.now(tz=timezone.utc).date()
    async with aiosqlite.connect(CONTEXT_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, kind, entity_type, name, context, due_date,
                      mention_count, last_mentioned_at
               FROM context_items WHERE user_id = ? AND status = 'open'""",
            (user_id,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    items = []
    for r in rows:
        if _is_stale(r.get("due_date"), r.get("last_mentioned_at", ""), today):
            continue
        r["priority"] = _priority(r, today)
        items.append(r)
    items.sort(key=lambda x: x["priority"], reverse=True)
    return items[:limit]


async def get_related(user_id: int, name: str, limit: int = 8) -> list[dict[str, Any]]:
    """Return items linked to the named item, strongest edge first."""
    await init_db()
    norm = _norm(name)
    async with aiosqlite.connect(CONTEXT_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name FROM context_items WHERE user_id = ?", (user_id,)
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        anchor = next((r["id"] for r in rows if _norm(r["name"]) == norm), None)
        if anchor is None:
            anchor = next((r["id"] for r in rows if _token_prefix_match(norm, _norm(r["name"]))), None)
        if anchor is None:
            return []
        async with db.execute(
            """SELECT a_id, b_id, weight FROM context_links
               WHERE user_id = ? AND (a_id = ? OR b_id = ?)
               ORDER BY weight DESC LIMIT ?""",
            (user_id, anchor, anchor, limit),
        ) as cur:
            edges = [dict(r) for r in await cur.fetchall()]
        out = []
        for e in edges:
            other = e["b_id"] if e["a_id"] == anchor else e["a_id"]
            async with db.execute(
                "SELECT name, entity_type FROM context_items WHERE id = ?", (other,)
            ) as cur:
                row = await cur.fetchone()
            if row:
                out.append({"name": row[0], "entity_type": row[1], "weight": e["weight"]})
    return out


async def stats(user_id: int) -> dict:
    await init_db()
    async with aiosqlite.connect(CONTEXT_DB) as db:
        async with db.execute(
            "SELECT kind, COUNT(*) FROM context_items WHERE user_id = ? AND status='open' GROUP BY kind",
            (user_id,),
        ) as cur:
            by_kind = {k: c for k, c in await cur.fetchall()}
        async with db.execute(
            "SELECT COUNT(*) FROM context_links WHERE user_id = ?", (user_id,)
        ) as cur:
            links = (await cur.fetchone())[0]
    return {"entities": by_kind.get("entity", 0), "intents": by_kind.get("intent", 0), "links": links}
