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

# Aliase: jeder Wert in der Liste gilt als identisch mit dem Schluessel
CLUB_ALIASES: dict[str, list[str]] = {
    "borussia dortmund": ["dortmund", "bvb", "bvb 09", "borussia dortmund"],
    "dynamo dresden":    ["dynamo", "sgd", "dynamo dresden"],
    "fc bayern":         ["bayern", "fc bayern muenchen", "fc bayern münchen", "fcb"],
    "rb leipzig":        ["leipzig", "rbl"],
    "bayer leverkusen":  ["leverkusen", "bayer"],
}


def _canonical(name: str) -> str:
    """Gibt den kanonischen Club-Namen zurueck (oder den Originalnamen falls kein Alias bekannt)."""
    normalized = re.sub(r"\(.*?\)", "", name).strip().lower()
    for canonical, aliases in CLUB_ALIASES.items():
        if normalized in aliases:
            return canonical
    return normalized


def _extract_clubs(memory: dict) -> list[str]:
    """Extrahiert Clubs aus Memory, dedupliziert per kanonischem Namen."""
    raw: list[str] = []
    for key in CLUB_KEYS:
        value = memory.get(key)
        if not value:
            continue
        for sep in [",", "/", ";", "|"]:
            if sep in value:
                raw.extend([v.strip() for v in value.split(sep) if v.strip()])
                break
        else:
            raw.append(value.strip())

    seen_canonical: set[str] = set()
    unique: list[str] = []
    for c in raw:
        canon = _canonical(c)
        if canon not in seen_canonical:
            seen_canonical.add(canon)
            # Bevorzuge die kanonische Schreibweise falls bekannt
            canonical_name = next(
                (k for k, aliases in CLUB_ALIASES.items() if canon in aliases),
                c  # fallback: Originalschreibweise
            )
            unique.append(canonical_name)
    return unique


def _cache_rows_to_ranked(rows: list[dict]) -> list[RankedNews]:
    """Konvertiert Cache-Rows in RankedNews-Objekte."""
    items = []
    for r in rows:
        source_str = r.get("source") or ""
        sources = [s.strip() for s in source_str.split(",") if s.strip()] or ["Unbekannt"]
        url = r.get("url") or ""
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
        return f"\n\n_\u23f0 Gecacht {age_label} \u00b7 N\u00e4chstes Update automatisch_"
    return f"\n\n_\u2705 Gecacht {age_label}_"


async def _llm_deduplicate_by_topic(
    items: list[RankedNews],
    club: str,
    max_items: int = TOP_N_OUTPUT,
) -> list[RankedNews]:
    """
    Sendet Titel-Liste ans LLM und bittet es, thematisch vielfaeltige Artikel auszuwaehlen.
    Das LLM waehlt NUR Indizes aus — es erfindet oder veraendert keine Inhalte.
    """
    if len(items) <= max_items:
        return items

    from app.services.openrouter_client import ask_llm  # korrekter Funktionsname

    titles_block = "\n".join(f"{i}. {item.title}" for i, item in enumerate(items))
    prompt = (
        f"Du bekommst eine nummerierte Liste von Nachrichten-Schlagzeilen \u00fcber {club} (Fu\u00dfball, 1. Mannschaft).\n"
        f"Deine Aufgabe: W\u00e4hle genau {max_items} Schlagzeilen aus, die thematisch m\u00f6glichst vielf\u00e4ltig sind.\n\n"
        f"Regeln:\n"
        f"- W\u00e4hle KEINE zwei Artikel zum selben Thema.\n"
        f"- W\u00e4hle KEINE Artikel \u00fcber andere Mannschaften (U23, Frauen, andere Sportarten).\n"
        f"- Bevorzuge Artikel mit konkreten, aktuellen Informationen.\n"
        f"- Ver\u00e4ndere, erg\u00e4nze oder erfinde KEINERLEI Inhalte. Du w\u00e4hlst nur aus.\n\n"
        f"Antworte AUSSCHLIE\u00dfLICH mit den Nummern der ausgew\u00e4hlten Artikel, kommasepariert.\n"
        f"Beispiel: 0,2,5,7,9\n\n"
        f"Schlagzeilen:\n{titles_block}"
    )

    try:
        response = await ask_llm(prompt, system_prompt="Du bist ein News-Selektor. Antworte nur mit Zahlen.")
        response = response.strip()
        indices = [int(x.strip()) for x in response.split(",") if x.strip().isdigit()]
        valid = [i for i in indices if 0 <= i < len(items)]
        valid = list(dict.fromkeys(valid))[:max_items]
        if len(valid) >= 3:
            logger.info("LLM-Dedup: %d \u2192 %d Artikel f\u00fcr %s", len(items), len(valid), club)
            return [items[i] for i in valid]
    except Exception as e:
        logger.warning("LLM-Dedup fehlgeschlagen f\u00fcr %s: %s \u2014 Fallback Top-%d", club, e, max_items)

    return items[:max_items]


async def _build_club_block(club: str, force_refresh: bool) -> str:
    """Laedt, dedupliziert und formatiert News fuer einen einzelnen Club."""
    try:
        if force_refresh:
            success = await refresh_club_cache(club)
            if not success:
                return f"\u26a0\ufe0f Refresh fehlgeschlagen f\u00fcr *{club}*. Zeige Cache-Version."

        rows = load_from_cache(club)
        if not rows or not is_cache_fresh(club):
            logger.info("Cache leer/abgelaufen f\u00fcr %s \u2014 Live-Fallback", club)
            await refresh_club_cache(club)
            rows = load_from_cache(club)

        if not rows:
            return f"Keine News f\u00fcr *{club}* gefunden."

        ranked = _cache_rows_to_ranked(rows)
        ranked = await _llm_deduplicate_by_topic(ranked, club, max_items=TOP_N_OUTPUT)
        block  = format_news_output(club, ranked)
        block += _freshness_label(club)
        return block

    except Exception as e:
        logger.error("Fehler beim Laden des Cache f\u00fcr %s: %s", club, e, exc_info=True)
        return f"\u26a0\ufe0f Fehler beim Abrufen der News f\u00fcr *{club}*."


async def fetch_news_for_user(user_id: int, force_refresh: bool = False) -> list[str]:
    """
    Gibt eine Liste von Nachrichten-Bloecken zurueck — einen Block pro Verein.
    Jeder Block wird als separate Telegram-Nachricht gesendet.
    """
    memory = get_confirmed(user_id)
    clubs  = _extract_clubs(memory)

    if not clubs:
        return [
            "Ich habe keine Lieblingsvereine in deiner Memory gefunden.\n\n"
            "Sag mir einfach: _\"Mein Lieblingsverein ist FC Bayern\"_ "
            "und ich merke es mir f\u00fcr das n\u00e4chste Mal!"
        ]

    logger.info("fetch_news_for_user | user=%d | clubs=%s | force=%s", user_id, clubs, force_refresh)

    blocks = []
    for club in clubs:
        block = await _build_club_block(club, force_refresh)
        blocks.append(block)

    return blocks
