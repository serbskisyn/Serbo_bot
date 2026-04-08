import logging
import asyncio
import re
from app.bot.memory import get_confirmed
from app.services.news_fetcher import fetch_club_news
from app.services.news_ranker import rank_news, enrich_ranked_news, format_news_output

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

    # Deduplizieren — auch Kurzformen rausfiltern
    # z.B. "Borussia Dortmund (BVB 09)" und "BVB 09" sind gleich
    seen_normalized: set[str] = set()
    unique = []
    for c in clubs:
        # Klammern + Inhalt entfernen für Vergleich
        normalized = re.sub(r"\(.*?\)", "", c).strip().lower()
        if normalized not in seen_normalized:
            seen_normalized.add(normalized)
            unique.append(c)

    return unique


async def fetch_news_for_user(user_id: int) -> str:
    memory = get_confirmed(user_id)
    clubs  = _extract_clubs(memory)

    if not clubs:
        return (
            "Ich habe keine Lieblingsvereine in deiner Memory gefunden.\n\n"
            "Sag mir einfach: _\"Mein Lieblingsverein ist FC Bayern\"_ "
            "und ich merke es mir fuer das naechste Mal!"
        )

    logger.info(f"fetch_news_for_user | user={user_id} | clubs={clubs}")

    # Sequentiell fetchen um GNews Rate Limit zu schonen
    output_blocks = []
    for club in clubs:
        try:
            result   = await fetch_club_news(club)
            ranked   = rank_news(result, top_n=10)
            enriched = await enrich_ranked_news(ranked, club)
            block    = format_news_output(club, enriched)
            output_blocks.append(block)
        except Exception as e:
            logger.error(f"Fehler beim Fetchen fuer {club}: {e}")
            output_blocks.append(f"Fehler beim Abrufen der News fuer *{club}*.")

    return "\n\n\n".join(output_blocks)