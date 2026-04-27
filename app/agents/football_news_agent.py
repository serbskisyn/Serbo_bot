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
    """Konvertiert Cache-Rows in RankedNews-Objekte.
    Stellt sicher, dass sources und urls immer Listen sind und korrekt zugeordnet.
    """
    items = []
    for r in rows:
        source_str = r.get("source") or ""
        # sources ist kommagetrennt im Cache gespeichert
        sources = [s.strip() for s in source_str.split(",") if s.strip()] or ["Unbekannt"]
        url = r.get("url") or ""
        # Jede Cache-Row hat genau eine URL — beim Clustering im Ranker werden mehrere zusammengeführt.
        # Hier bauen wir pro Source-Eintrag eine URL-Liste mit der einzigen verfügbaren URL.
        urls = [url] * len(sources) if url else []

        items.append(RankedNews(
            title=r.get("title") or "",
            snippet=r.get("snippet") or "",
            sources=sources,
            urls=urls,
            score=int(r.get("score") or 0),
            published=r.get("published") or "",
        ))
    return items


def _freshness_label(club_name: str) -> str:
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
    Sendet Titel-Liste ans LLM und bittet es, thematische Duplikate zu entfernen.
    Das LLM bewertet NUR nach Themendiversität — es darf keine Inhalte ergänzen oder verändern.
    """
    if len(items) <= max_items:
        return items

    from app.services.openrouter_client import call_llm_raw

    titles_block = "\n".join(f"{i}. {item.title}" for i, item in enumerate(items))
    prompt = (
        f"Du bekommst eine nummerierte Liste von Nachrichten-Schlagzeilen über {club} (Fußball, 1. Mannschaft).\n"
        f"Deine Aufgabe: Wähle genau {max_items} Schlagzeilen aus, die thematisch möglichst vielfältig sind.\n\n"
        f"Regeln:\n"
        f"- Wähle KEINE zwei Artikel zum selben Thema (z.B. zwei Transfer-Artikel über denselben Spieler).\n"
        f"- Wähle KEINE Artikel über andere Mannschaften (U23, Frauen, andere Sportarten).\n"
        f"- Bevorzuge Artikel mit konkreten, aktuellen Informationen gegenüber vagen Spekulationen.\n"
        f"- Verändere, ergänze oder erfünde KEINERLEI Inhalte. Du wählst nur aus.\n\n"
        f"Antworte AUSSCHLIEßLICH mit den Nummern der ausgewählten Artikel, kommasepariert.\n"
        f"Beispiel: 0,2,5,7,9\n\n"
        f"Schlagzeilen:\n{titles_block}"
    )

    try:
        response = await call_llm_raw(prompt, max_tokens=50)
        response = response.strip()
        indices = [int(x.strip()) for x in response.split(",") if x.strip().isdigit()]
        valid = [i for i in indices if 0 <= i < len(items)]
        valid = list(dict.fromkeys(valid))[:max_items]
        if len(valid) >= 3:
            logger.info("LLM-Dedup: %d → %d Artikel für %s", len(items), len(valid), club)
            return [items[i] for i in valid]
    except Exception as e:
        logger.warning("LLM-Dedup fehlgeschlagen für %s: %s — Fallback Top-%d", club, e, max_items)

    return items[:max_items]


async def _build_club_block(club: str, force_refresh: bool) -> str:
    """Laed, dedupliziert und formatiert News für einen einzelnen Club."""
    try:
        if force_refresh:
            success = await refresh_club_cache(club)
            if not success:
                return f"⚠️ Refresh fehlgeschlagen für *{club}*. Zeige Cache-Version."

        rows = load_from_cache(club)
        if not rows or not is_cache_fresh(club):
            logger.info("Cache leer/abgelaufen für %s — Live-Fallback", club)
            await refresh_club_cache(club)
            rows = load_from_cache(club)

        if not rows:
            return f"Keine News für *{club}* gefunden."

        ranked = _cache_rows_to_ranked(rows)
        ranked = await _llm_deduplicate_by_topic(ranked, club, max_items=TOP_N_OUTPUT)
        block  = format_news_output(club, ranked)
        block += _freshness_label(club)
        return block

    except Exception as e:
        logger.error("Fehler beim Laden des Cache für %s: %s", club, e)
        return f"Fehler beim Abrufen der News für *{club}*."


async def fetch_news_for_user(user_id: int, force_refresh: bool = False) -> list[str]:
    """
    Gibt eine Liste von Nachrichten-Blöcken zurück — einen Block pro Verein.
    Jeder Block wird als separate Telegram-Nachricht gesendet.
    """
    memory = get_confirmed(user_id)
    clubs  = _extract_clubs(memory)

    if not clubs:
        return [
            "Ich habe keine Lieblingsvereine in deiner Memory gefunden.\n\n"
            "Sag mir einfach: _\"Mein Lieblingsverein ist FC Bayern\"_ "
            "und ich merke es mir für das nächste Mal!"
        ]

    logger.info("fetch_news_for_user | user=%d | clubs=%s | force=%s", user_id, clubs, force_refresh)

    blocks = []
    for club in clubs:
        block = await _build_club_block(club, force_refresh)
        blocks.append(block)

    return blocks
