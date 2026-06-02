"""
notes_index.py — semantic recall over the daily markdown summaries.

The evening reflection and session summary write markdown into
app/data/summaries/{user_id}_{date}[_reflection].md, but until now those
files were write-only — the bot never read them back. This module embeds
them into the semantic.db 'notes' collection and lets the general agent
recall the most relevant past notes for the current question.

Indexing is mtime-gated (a small JSON state tracks the last-indexed mtime
per file) so we only re-embed changed files. ref_ids are a stable hash of
filename+chunk-index, and semantic.store() is delete-then-insert, so
re-indexing the same file is idempotent.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from app.config import RECALL_ENABLED, RECALL_TOP_K
from app.services import semantic

logger = logging.getLogger(__name__)

_SUMMARY_DIR = Path(__file__).parent.parent / "data" / "summaries"
_STATE_FILE = Path(__file__).parent.parent / "data" / "notes_index_state.json"

_CHUNK_MAX_CHARS = 600

# filename like  355857037_2026-06-02_reflection.md  or  355857037_2026-06-02.md
_NAME_RE = re.compile(r"^(\d+)_(\d{4}-\d{2}-\d{2})")


def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("notes_index: state save failed: %s", exc)


def _parse_filename(path: Path) -> tuple[int, str] | None:
    m = _NAME_RE.match(path.name)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def _ref_id(filename: str, idx: int) -> int:
    h = hashlib.sha1(f"{filename}:{idx}".encode("utf-8")).hexdigest()[:15]
    return int(h, 16)  # < 2**60, safe for SQLite INTEGER


def _chunk(text: str) -> list[str]:
    """Split markdown into ~600-char chunks on paragraph boundaries."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 <= _CHUNK_MAX_CHARS:
            cur = f"{cur}\n\n{p}" if cur else p
        else:
            if cur:
                chunks.append(cur)
            cur = p[:_CHUNK_MAX_CHARS] if len(p) > _CHUNK_MAX_CHARS else p
    if cur:
        chunks.append(cur)
    return chunks


async def index_file(path: Path) -> int:
    """Embed one summary file into the notes collection. Returns chunks stored."""
    parsed = _parse_filename(path)
    if parsed is None:
        return 0
    user_id, day = parsed
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("notes_index: read %s failed: %s", path.name, exc)
        return 0

    stored = 0
    for idx, chunk in enumerate(_chunk(text)):
        # Prefix the date so recalled context is self-dating
        payload = f"[{day}] {chunk}"
        ok = await semantic.store("notes", _ref_id(path.name, idx), user_id, payload)
        if ok:
            stored += 1
    return stored


async def reindex(force: bool = False) -> int:
    """Index all summary files whose mtime changed since last run.
    Returns total chunks (re)stored."""
    if not _SUMMARY_DIR.exists():
        return 0
    state = _load_state()
    total = 0
    for path in sorted(_SUMMARY_DIR.glob("*.md")):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if not force and state.get(path.name) == mtime:
            continue
        n = await index_file(path)
        if n:
            state[path.name] = mtime
            total += n
            logger.info("notes_index: indexed %s (%d chunks)", path.name, n)
    _save_state(state)
    return total


async def recall(user_id: int, query: str, k: int | None = None) -> list[str]:
    """Return up to k past-note chunks most relevant to the query text."""
    if not RECALL_ENABLED:
        return []
    query = (query or "").strip()
    if len(query) < 4:
        return []
    k = k or RECALL_TOP_K
    try:
        hits = await semantic.find_similar(
            "notes", user_id, query,
            threshold=semantic.DIST_LOOSE_RECALL, limit=k,
        )
    except Exception as exc:
        logger.debug("notes_index: recall failed: %s", exc)
        return []
    return [text for _ref, text, _dist in hits if text]


def recall_block(chunks: list[str]) -> str:
    """Render recalled chunks as a German prompt-context block."""
    if not chunks:
        return ""
    lines = ["\nRelevante frühere Notizen/Reflexionen (nur nutzen wenn passend):"]
    for c in chunks:
        snippet = c.replace("\n", " ").strip()
        lines.append(f"- {snippet[:300]}")
    return "\n".join(lines)
