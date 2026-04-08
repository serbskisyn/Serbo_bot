import re
import logging
import asyncio
from dataclasses import dataclass, field
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
        "fc", "sc", "sv", "vfb", "the", "a", "of", "at", "for",
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


def _cluster(items: list, get_title) -> list[list[int]]:
    """Generisches Clustering via Jaccard auf Titel."""
    token_sets = [_normalize(get_title(i)) for i in items]
    clusters: list[list[int]] = []
    assigned = [False] * len(items)

    for i in range(len(items)):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, len(items)):
            if assigned[j]:
                continue
            if _jaccard(token_sets[i], token_sets[j]) >= SIMILARITY_THRESHOLD:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)
    return clusters


def rank_news(items: list[NewsItem], top_n: int = 10) -> list[RankedNews]:
    if not items:
        return []

    # URL-Deduplizierung
    seen_urls: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            unique.append(item)

    # Clustering auf Originaltitel
    clusters = _cluster(unique, lambda x: x.title)

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


def _re_cluster(items: list[RankedNews]) -> list[RankedNews]:
    """
    Zweites Clustering nach LLM-Enrichment auf deutschen Titeln.
    Fasst gleiche Meldungen zusammen die LLM ähnlich übersetzt hat.
    """
    if not items:
        return []

    clusters = _cluster(items, lambda x: x.title)
    merged: list[RankedNews] = []

    for cluster in clusters:
        cluster_items = [items[i] for i in cluster]

        # Alle Quellen + URLs zusammenführen
        seen_sources: set[str] = set()
        all_sources: list[str] = []
        all_urls:    list[str] = []
        for item in cluster_items:
            for src, url in zip(item.sources, item.urls):
                if src not in seen_sources:
                    seen_sources.add(src)
                    all_sources.append(src)
                    all_urls.append(url)

        # Bestes Snippet (längster Text)
        best = max(cluster_items, key=lambda x: len(x.snippet))

        merged.append(RankedNews(
            title=best.title,
            snippet=best.snippet,
            sources=all_sources,
            urls=all_urls,
            score=len(all_sources),
            published=max(
                (i.published for i in cluster_items if i.published),
                default=""
            ),
        ))

    merged.sort(key=lambda x: (x.score, x.published), reverse=True)
    return merged


async def enrich_ranked_news(ranked: list[RankedNews], club: str) -> list[RankedNews]:
    """
    1. Parallel LLM-Enrichment aller Items
    2. Re-Clustering auf deutschen Titeln
    """
    from app.services.news_enricher import enrich_news_item

    async def _enrich_one(item: RankedNews) -> RankedNews | None:
        enriched = await enrich_news_item(
            url=item.urls[0],
            title=item.title,
            snippet=item.snippet,
            club=club,
        )
        if enriched is None:
            return None
        item.title   = enriched["headline"]
        item.snippet = enriched["snippet"]
        item.urls[0] = enriched["url"]
        return item

    results  = await asyncio.gather(*[_enrich_one(r) for r in ranked])
    enriched = [r for r in results if r is not None]

    # Zweites Clustering auf deutschen Titeln
    re_clustered = _re_cluster(enriched)
    logger.info(f"Re-Clustering: {len(enriched)} → {len(re_clustered)} News")
    return re_clustered


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