import logging
import asyncio
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

    seen = set()
    unique = []
    for c in clubs:
        if c.lower() not in seen:
            seen.add(c.lower())
            unique.append(c)
    return unique


async def fetch_news_for_user(user_id: int) -> str:
    memory = get_confirmed(user_id)
    clubs  = _extract_clubs(memory)

    if not clubs:
        return (
            "⚽ Ich habe keine Lieblingsvereine in deiner Memory gefunden.\n\n"
            "Sag mir einfach: _\"Mein Lieblingsverein ist FC Bayern\"_ "
            "und ich merke es mir für das nächste Mal!"
        )

    logger.info(f"fetch_news_for_user | user={user_id} | clubs={clubs}")

    fetch_tasks = [fetch_club_news(club) for club in clubs]
    results     = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    output_blocks = []
    for club, result in zip(clubs, results):
        if isinstance(result, Exception):
            logger.error(f"Fehler beim Fetchen für {club}: {result}")
            output_blocks.append(f"⚽ *{club}* – Fehler beim Abrufen der News.")
            continue

        ranked   = rank_news(result, top_n=10)
        enriched = await enrich_ranked_news(ranked)
        block    = format_news_output(club, enriched)
        output_blocks.append(block)

    return "\n\n\n".join(output_blocks)