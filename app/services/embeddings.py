"""
embeddings.py — Async wrapper around the LiteLLM embedding model + cache.

Uses gemini-embedding-2 (3072-dim) via the LiteLLM proxy. NOTE: switching the
embedding model invalidates any previously-stored vectors (different model =
different vector space), so semantic.db + the cache must be rebuilt on switch.

A tiny on-disk cache (SHA-256-of-text → vector) prevents re-embedding
the same exact string. Cache lives next to semantic.db.

Returns lists of floats so callers can pass them straight to sqlite-vec.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import struct
from pathlib import Path

import httpx

from app.config import LITELLM_API_KEY, LITELLM_BASE_URL, LLM_EMBED_MODEL

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = LLM_EMBED_MODEL
EMBEDDING_DIM = 3072

CACHE_FILE = Path(__file__).parent.parent / "data" / "embedding_cache.bin"

_lock = asyncio.Lock()
_cache: dict[str, list[float]] = {}
_cache_loaded = False


def _digest(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def _load_cache() -> None:
    """Binary cache format: 32B SHA-256 + 4B float32 dim + N*4B floats."""
    global _cache_loaded
    if _cache_loaded:
        return
    if not CACHE_FILE.exists():
        _cache_loaded = True
        return
    try:
        data = CACHE_FILE.read_bytes()
        offset = 0
        record_size = 32 + EMBEDDING_DIM * 4
        while offset + record_size <= len(data):
            key = data[offset:offset + 32].hex()
            vec_bytes = data[offset + 32:offset + record_size]
            vec = list(struct.unpack(f"{EMBEDDING_DIM}f", vec_bytes))
            _cache[key] = vec
            offset += record_size
        logger.info("embeddings: loaded %d cached vectors from %s", len(_cache), CACHE_FILE.name)
    except Exception as exc:
        logger.warning("embeddings: cache load failed: %s", exc)
    _cache_loaded = True


def _append_cache(text: str, vec: list[float]) -> None:
    """Append-only cache write — avoids rewriting the whole file."""
    if len(vec) != EMBEDDING_DIM:
        return
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        key_bytes = bytes.fromhex(_digest(text))
        with CACHE_FILE.open("ab") as f:
            f.write(key_bytes)
            f.write(struct.pack(f"{EMBEDDING_DIM}f", *vec))
    except Exception as exc:
        logger.warning("embeddings: cache append failed: %s", exc)


async def embed(text: str) -> list[float] | None:
    """Return the embedding vector for `text`, or None on failure.

    Hits the on-disk cache first. Updates the cache after a fresh API call.
    """
    text = (text or "").strip()
    if not text:
        return None

    _load_cache()
    key = _digest(text)

    async with _lock:
        cached = _cache.get(key)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"{LITELLM_BASE_URL.rstrip('/')}/embeddings",
                json={"model": EMBEDDING_MODEL, "input": text},
                headers={
                    "Authorization": f"Bearer {LITELLM_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            r.raise_for_status()
            payload = r.json()
    except Exception as exc:
        logger.warning("embeddings: API call failed for %r: %s", text[:60], exc)
        return None

    try:
        vec = payload["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        logger.warning("embeddings: unexpected payload shape: %s", exc)
        return None

    if not isinstance(vec, list) or len(vec) != EMBEDDING_DIM:
        logger.warning("embeddings: unexpected dim=%s for %r", len(vec) if isinstance(vec, list) else "?", text[:60])
        return None

    async with _lock:
        _cache[key] = vec
    _append_cache(text, vec)
    return vec
