import re
import logging
import asyncio
from dataclasses import dataclass
from app.services.news_fetcher import NewsItem

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.35
MEDALS = ["🥇", "🥈", "🥉"]


@dataclass
class RankedNews:
    title:     str
    snippet:   str
    sources:   list[str]
    urls:      list[str]
    score:     int
    published: str = ""


def _normalize(text: str) -> set[str]:
    stopwords = {
        "der", "die", "das", "und", "in", "im", "am", "bei", "für", "von",
        "mit", "nach", "an", "zu", "auf", "ist", "ein", "eine", "des",
        "fc", "sc", "sv", "vfb", "the", "a", "of", "in", "at", "for",
        "to", "is", "and", "with", "after", "as", "by",
    }
    tokens = re.findall(r"[a-zäöüß]{3,}", text.lower())
    return {t for t in tokens if t not in stopwords}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _best_snippet(snippets: list[str]) -> str:
    clean = [s for s in snippets if s]
    if not clean:
        return ""
    return max(clean, key=lambda s: len(s.split()))


def _format_date(item: NewsItem) -> str:
    if item.published is None:
        return ""
    return item.published.strftime("%d.%m.%Y %H:%M")


def rank_news(items: list[NewsItem], top_n: int = 10) -> list[RankedNews]:
    if not items:
        return []

    seen_urls: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            unique.append(item)

    token_sets = [_normalize(item.title) for item in unique]
    clusters:  list[list[int]] = []
    assigned = [False] * len(unique)

    for i in range(len(unique)):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, len(unique)):
            if assigned[j]:
                continue
            if _jaccard(token_sets[i], token_sets[j]) >= SIMILARITY_THRESHOLD:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)

    ranked: list[RankedNews] = []
    for cluster in clusters:
        cluster_items = [unique[i] for i in cluster]
        best_title    = max(cluster_items, key=lambda x: len(x.title)).title
        source_map: dict[str, str] = {}
        for item in cluster_items:
            if item.source not in source_map:
                source_map[item.source] = item.url

        snippet = _best_snippet([i.snippet for i in cluster_items])
        dated   = [i for i in cluster_items if i.published]
        pub_str = max(dated, key=lambda x: x.published).published.strftime("%d.%m.%Y %H:%M") if dated else ""

        ranked.append(RankedNews(
            title=best_title,
            snippet=snippet,
            sources=list(source_map.keys()),
            urls=list(source_map.values()),
            score=len(source_map),
            published=pub_str,
        ))

    ranked.sort(key=lambda x: (x.score, x.published), reverse=True)
    return ranked[:top_n]


async def enrich_ranked_news(ranked: list[RankedNews], club: str) -> list[RankedNews]:
    """Reichert alle RankedNews parallel an — filtert irrelevante Artikel raus."""
    from app.services.news_enricher import enrich_news_item

    async def _enrich_one(item: RankedNews) -> RankedNews | None:
        enriched = await enrich_news_item(item.urls[0], item.title, club)
        if enriched is None:
            return None
        item.title   = enriched["headline"]
        item.snippet = enriched["snippet"]
        item.urls[0] = enriched["url"]
        return item

    results = await asyncio.gather(*[_enrich_one(r) for r in ranked])
    return [r for r in results if r is not None]


def format_news_output(club_name: str, ranked: list[RankedNews]) -> str:
    if not ranked:
        return f"⚽ *{club_name}* – Keine aktuellen News gefunden (letzte 48h)."

    lines = [f"⚽ *{club_name}* – Top News\n{'─' * 30}"]

    for i, news in enumerate(ranked):
        medal        = MEDALS[i] if i < 3 else f"{i + 1}."
        source_count = f"[{news.score} {'Quelle' if news.score == 1 else 'Quellen'}]"

        lines.append(f"\n{medal} {source_count} *{news.title}*")

        if news.snippet:
            lines.append(f"{news.snippet}")

        if news.published:
            lines.append(f"🕐 {news.published}")

        source_links = " · ".join(
            f"[{_domain(url)}]({url})"
            for url in news.urls
        )
        lines.append(source_links)

    return "\n".join(lines)


def _domain(url: str) -> str:
    try:
        host = url.split("/")[2]
        return host.replace("www.", "")
    except Exception:
        return url