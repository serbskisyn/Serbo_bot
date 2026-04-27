"""
news_cache.py
-------------
SQLite-basierter News-Cache mit Background-Scheduler.
Fetcht Nachrichten im Hintergrund in unregelmaessigen Intervallen
— unabhaengig von User-Anfragen. User bekommen immer gecachte Version.
"""

import asyncio
import logging
import random
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.config import (
    NEWS_CACHE_DB_PATH,
    NEWS_FAVORITE_CLUBS,
    NEWS_SCHEDULER_BASE_MINUTES,
    NEWS_SCHEDULER_JITTER_MINUTES,
    NEWS_CACHE_MAX_AGE_HOURS,
    NEWS_STALE_LABEL_HOURS,
)
from app.services.news_fetcher import fetch_club_news
from app.services.news_ranker import rank_news, enrich_ranked_news

logger = logging.getLogger(__name__)


# ── DB Setup ────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    db_path = Path(NEWS_CACHE_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_cache_db() -> None:
    """Erstellt die Cache-Tabellen falls nicht vorhanden."""
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_cache (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            club_name    TEXT    NOT NULL,
            title        TEXT    NOT NULL,
            url          TEXT    NOT NULL,
            source       TEXT,
            published    TEXT,
            snippet      TEXT,
            score        REAL    DEFAULT 0.0,
            cached_at    TEXT    NOT NULL,
            UNIQUE(club_name, url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_meta (
            club_name    TEXT PRIMARY KEY,
            last_refresh TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    logger.info("News-Cache DB initialisiert: %s", NEWS_CACHE_DB_PATH)


# ── Cache schreiben ───────────────────────────────────────────────────────────

def _pub_to_str(published) -> str | None:
    """
    Normalisiert published auf einen String fuer SQLite.
    Akzeptiert: datetime, str, None.
    """
    if published is None:
        return None
    if isinstance(published, datetime):
        return published.isoformat()
    if isinstance(published, str):
        return published if published.strip() else None
    return str(published)


def _save_to_cache(club_name: str, enriched_items: list) -> None:
    """
    Speichert RankedNews-Objekte fuer einen Club in den Cache.
    RankedNews hat: title, snippet, sources (list), urls (list), score (int), published (str)
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db()
    try:
        conn.execute("DELETE FROM news_cache WHERE club_name = ?", (club_name,))

        for item in enriched_items:
            # RankedNews: sources=list, urls=list — erste URL + Quellen als kommagetrennt
            if hasattr(item, "urls") and item.urls:
                primary_url = item.urls[0]
                source_str  = ", ".join(item.sources) if hasattr(item, "sources") else ""
            else:
                primary_url = getattr(item, "url", "")
                source_str  = getattr(item, "source", "")

            pub = _pub_to_str(getattr(item, "published", None))

            conn.execute("""
                INSERT OR REPLACE INTO news_cache
                    (club_name, title, url, source, published, snippet, score, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                club_name,
                item.title,
                primary_url,
                source_str,
                pub,
                item.snippet,
                getattr(item, "score", 0),
                now,
            ))

        conn.execute("""
            INSERT OR REPLACE INTO cache_meta (club_name, last_refresh)
            VALUES (?, ?)
        """, (club_name, now))
        conn.commit()
        logger.info("Cache aktualisiert: %s | %d Artikel", club_name, len(enriched_items))
    except Exception as e:
        logger.error("Cache-Schreibfehler (%s): %s", club_name, e)
    finally:
        conn.close()


# ── Cache lesen ───────────────────────────────────────────────────────────────

def get_cache_meta(club_name: str) -> dict | None:
    """Gibt Metadaten des letzten Refreshes zurueck oder None."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT last_refresh FROM cache_meta WHERE club_name = ?",
            (club_name,)
        ).fetchone()
        if not row:
            return None
        return {"last_refresh": datetime.fromisoformat(row["last_refresh"])}
    finally:
        conn.close()


def is_cache_fresh(club_name: str) -> bool:
    """True wenn Cache juenger als NEWS_CACHE_MAX_AGE_HOURS."""
    meta = get_cache_meta(club_name)
    if not meta:
        return False
    age = datetime.now(timezone.utc) - meta["last_refresh"]
    return age.total_seconds() < NEWS_CACHE_MAX_AGE_HOURS * 3600


def get_cache_age_label(club_name: str) -> str:
    """Gibt lesbares Alter des Caches zurueck, z.B. 'vor 34 Min' oder 'vor 2 Std'."""
    meta = get_cache_meta(club_name)
    if not meta:
        return "kein Cache"
    age = datetime.now(timezone.utc) - meta["last_refresh"]
    minutes = int(age.total_seconds() // 60)
    if minutes < 60:
        return f"vor {minutes} Min"
    hours = minutes // 60
    return f"vor {hours} Std"


def load_from_cache(club_name: str) -> list[dict]:
    """Laed gecachte Artikel fuer einen Club als Liste von Dicts."""
    conn = _get_db()
    try:
        rows = conn.execute("""
            SELECT title, url, source, published, snippet, score
            FROM news_cache
            WHERE club_name = ?
            ORDER BY score DESC, published DESC
        """, (club_name,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Refresh-Logik ─────────────────────────────────────────────────────────────

async def refresh_club_cache(club_name: str) -> bool:
    """Fetcht frische News fuer einen Club und speichert in Cache. Gibt True bei Erfolg."""
    logger.info("Cache Refresh gestartet: %s", club_name)
    try:
        items    = await fetch_club_news(club_name)
        ranked   = rank_news(items, top_n=10)
        enriched = await enrich_ranked_news(ranked, club_name)
        _save_to_cache(club_name, enriched)
        return True
    except Exception as e:
        logger.error("Refresh fehlgeschlagen (%s): %s", club_name, e)
        return False


# ── Background Scheduler ──────────────────────────────────────────────────────

async def _scheduler_loop() -> None:
    """Laeuft als asyncio.Task — refresht Favoriten-Clubs in unregelmaessigen Intervallen."""
    logger.info(
        "News-Scheduler gestartet | Clubs: %s | Basis: %dmin | Jitter: ±%dmin",
        NEWS_FAVORITE_CLUBS, NEWS_SCHEDULER_BASE_MINUTES, NEWS_SCHEDULER_JITTER_MINUTES
    )

    # Beim Start: alle Favoriten initial laden (mit Versatz zwischen Clubs)
    for i, club in enumerate(NEWS_FAVORITE_CLUBS):
        if i > 0:
            startup_delay = random.uniform(30, 90)
            logger.info("Startup-Delay %ds fuer %s", int(startup_delay), club)
            await asyncio.sleep(startup_delay)
        await refresh_club_cache(club)

    # Danach: periodisch alle Favoriten refreshen
    while True:
        jitter_seconds = random.randint(
            -NEWS_SCHEDULER_JITTER_MINUTES * 60,
            NEWS_SCHEDULER_JITTER_MINUTES * 60
        )
        wait_seconds = NEWS_SCHEDULER_BASE_MINUTES * 60 + jitter_seconds
        next_run = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
        logger.info(
            "Naechster News-Refresh: %s (in %.0f min)",
            next_run.strftime("%H:%M UTC"),
            wait_seconds / 60
        )
        await asyncio.sleep(wait_seconds)

        for i, club in enumerate(NEWS_FAVORITE_CLUBS):
            if i > 0:
                inter_club_delay = random.uniform(30, 120)
                logger.info("Inter-Club-Delay %.0fs vor %s", inter_club_delay, club)
                await asyncio.sleep(inter_club_delay)
            await refresh_club_cache(club)


def start_background_scheduler() -> asyncio.Task:
    """Startet den Background-Scheduler als asyncio.Task. In main.py aufrufen."""
    init_cache_db()
    return asyncio.create_task(_scheduler_loop())
