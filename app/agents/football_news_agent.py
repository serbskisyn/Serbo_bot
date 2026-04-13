import logging
import re
from datetime import datetime, timezone, timedelta

from app.bot.memory import get_confirmed
from app.config import NEWS_STALE_LABEL_HOURS, NEWS_CACHE_MAX_AGE_HOURS
from app.services.news_cache import (
    load_from_cache, get_cache_age_label, get_cache_meta, is_cache_fresh, refresh_club_cache
)
from app.services.news_ranker import format_news_output
from app.services.news_fetcher import NewsItem

logger = logging.getLogger(__name__)

CLUB_KEYS = [
    "lieblingsverein", "lieblingsvereine", "verein", "vereine",
    "clubs", "club", "fussballverein", "fussballvereine", "favoriten",
]


def _extract_clubs(memory: dict) -> list[str]:
    clubs = []
    for key in CLUB_KEYS:
        value = memory.get(key)
        if not value:
            continue
        for sep in [",", "/", ";", "|"]:
            if sep in value:
                clubs.extend([v.strip() for v in value.split(sep) if v.strip()])
                break
        else:
            clubs.append(value.strip())

    seen_normalized: set[str] = set()
    unique = []
    for c in clubs:
        normalized = re.sub(r"\(.*?\)", "", c).strip().lower()
        if normalized not in seen_normalized:
            seen_normalized.add(normalized)
            unique.append(c)
    return unique


def _cache_rows_to_items(rows: list[dict]) -> list[NewsItem]:
    """Konvertiert Cache-Rows zurueck in NewsItem-Objekte fuer format_news_output."""
    items = []
    for r in rows:
        pub = None
        if r.get("published"):
            try:
                pub = datetime.fromisoformat(r["published"])
            except ValueError:
                pass
        item = NewsItem(
            title=r["enriched"] or r["title"],
            url=r["url"],
            source=r["source"] or "",
            published=pub,
            snippet=r["snippet"] or "",
        )
        items.append(item)
    return items


def _freshness_label(club_name: str) -> str:
    """Gibt ein Freshness-Label zurueck das an die News-Ausgabe angehaengt wird."""
    meta = get_cache_meta(club_name)
    if not meta:
        return ""
    age = datetime.now(timezone.utc) - meta["last_refresh"]
    age_label = get_cache_age_label(club_name)
    age_hours = age.total_seconds() / 3600

    if age_hours > NEWS_STALE_LABEL_HOURS:
        return f"\n\n_🕐 Gecacht {age_label} · Nächstes Update automatisch_"
    return f"\n\n_✅ Gecacht {age_label}_"


async def fetch_news_for_user(user_id: int, force_refresh: bool = False) -> str:
    memory = get_confirmed(user_id)
    clubs  = _extract_clubs(memory)

    if not clubs:
        return (
            "Ich habe keine Lieblingsvereine in deiner Memory gefunden.\n\n"
            "Sag mir einfach: _\"Mein Lieblingsverein ist FC Bayern\"_ "
            "und ich merke es mir fuer das naechste Mal!"
        )

    logger.info("fetch_news_for_user | user=%d | clubs=%s | force=%s", user_id, clubs, force_refresh)

    output_blocks = []
    for club in clubs:
        try:
            # Force-Refresh: Live-Fetch und Cache aktualisieren
            if force_refresh:
                logger.info("Force-Refresh fuer %s", club)
                success = await refresh_club_cache(club)
                if not success:
                    output_blocks.append(f"⚠️ Refresh fehlgeschlagen fuer *{club}*. Zeige Cache-Version.")

            # Cache laden
            rows = load_from_cache(club)

            # Falls Cache leer oder abgelaufen: einmaliger Live-Fetch als Fallback
            if not rows or not is_cache_fresh(club):
                logger.info("Cache leer/abgelaufen fuer %s — Live-Fallback", club)
                await refresh_club_cache(club)
                rows = load_from_cache(club)

            if not rows:
                output_blocks.append(f"Keine News fuer *{club}* gefunden.")
                continue

            items = _cache_rows_to_items(rows)
            block = format_news_output(club, items)
            block += _freshness_label(club)
            output_blocks.append(block)

        except Exception as e:
            logger.error("Fehler beim Laden des Cache fuer %s: %s", club, e)
            output_blocks.append(f"Fehler beim Abrufen der News fuer *{club}*.")

    return "\n\n\n".join(output_blocks)
