import logging
import re
from datetime import datetime, timezone

from app.bot.memory import get_confirmed
from app.config import NEWS_STALE_LABEL_HOURS
from app.services.news_cache import (
    load_from_cache, get_cache_age_label, get_cache_meta, is_cache_fresh, refresh_club_cache
)
from app.services.news_ranker import format_news_output, RankedNews, TOP_N_OUTPUT

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


def _cache_rows_to_ranked(rows: list[dict]) -> list[RankedNews]:
    """Konvertiert Cache-Rows in RankedNews-Objekte fuer format_news_output."""
    items = []
    for r in rows:
        source_str = r.get("source") or ""
        sources = [s.strip() for s in source_str.split(",") if s.strip()] or ["Unbekannt"]
        url = r.get("url") or ""

        items.append(RankedNews(
            title=r.get("title") or "",
            snippet=r.get("snippet") or "",
            sources=sources,
            urls=[url],
            score=int(r.get("score") or 0),
            published=r.get("published") or "",
        ))
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


async def _llm_deduplicate_by_topic(
    items: list[RankedNews],
    club: str,
    max_items: int = TOP_N_OUTPUT,
) -> list[RankedNews]:
    """
    Sendet Titel-Liste ans LLM und bittet es, thematische Duplikate
    zu entfernen und nur max_items diverse Themen zurückzugeben.
    Gibt Indizes der zu behaltenden Artikel zurück.
    """
    if len(items) <= max_items:
        return items

    from app.services.openrouter_client import call_llm_raw

    titles_block = "\n".join(f"{i}. {item.title}" for i, item in enumerate(items))
    prompt = (
        f"Du bekommst eine Liste von Sportnews-Schlagzeilen über {club}.\n"
        f"Wähle die {max_items} thematisch vielfältigsten und wichtigsten Artikel aus.\n"
        f"Vermeide dabei thematische Doppelungen (z.B. zwei Artikel zum selben Transfer, "
        f"selben Spiel oder selben Spieler).\n"
        f"Antworte NUR mit den Nummern (0-basiert) der ausgewählten Artikel, "
        f"kommasepariert, z.B.: 0,2,4,7,9\n\n"
        f"Schlagzeilen:\n{titles_block}"
    )

    try:
        response = await call_llm_raw(prompt, max_tokens=50)
        response = response.strip()
        indices = [int(x.strip()) for x in response.split(",") if x.strip().isdigit()]
        # Validierung: nur gültige Indizes, max max_items
        valid = [i for i in indices if 0 <= i < len(items)]
        valid = list(dict.fromkeys(valid))[:max_items]  # dedup, limit
        if len(valid) >= 3:  # mindestens 3 valide → LLM-Ergebnis nutzen
            logger.info("LLM-Dedup: %d → %d Artikel für %s (Indizes: %s)", len(items), len(valid), club, valid)
            return [items[i] for i in valid]
    except Exception as e:
        logger.warning("LLM-Dedup fehlgeschlagen für %s: %s — Fallback auf Top-%d", club, e, max_items)

    # Fallback: einfach Top-N
    return items[:max_items]


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
            if force_refresh:
                logger.info("Force-Refresh fuer %s", club)
                success = await refresh_club_cache(club)
                if not success:
                    output_blocks.append(f"⚠️ Refresh fehlgeschlagen fuer *{club}*. Zeige Cache-Version.")

            rows = load_from_cache(club)

            if not rows or not is_cache_fresh(club):
                logger.info("Cache leer/abgelaufen fuer %s — Live-Fallback", club)
                await refresh_club_cache(club)
                rows = load_from_cache(club)

            if not rows:
                output_blocks.append(f"Keine News fuer *{club}* gefunden.")
                continue

            ranked = _cache_rows_to_ranked(rows)

            # LLM-basiertes Themen-Deduping → Top 5 diverse Artikel
            ranked = await _llm_deduplicate_by_topic(ranked, club, max_items=TOP_N_OUTPUT)

            block  = format_news_output(club, ranked)
            block += _freshness_label(club)
            output_blocks.append(block)

        except Exception as e:
            logger.error("Fehler beim Laden des Cache fuer %s: %s", club, e)
            output_blocks.append(f"Fehler beim Abrufen der News fuer *{club}*.")

    return "\n\n\n".join(output_blocks)
