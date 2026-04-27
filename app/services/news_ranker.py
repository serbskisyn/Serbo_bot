import re
import logging
import asyncio
from dataclasses import dataclass, field
from app.services.news_fetcher import NewsItem

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.25
ENTITY_BOOST_WORDS   = 3
MEDALS = ["🥇", "🥈", "🥉"]
TOP_N_OUTPUT = 5
MAX_SOURCES_PER_NEWS = 3  # Maximal 3 Quellen pro Nachricht anzeigen


@dataclass
class RankedNews:
    title:     str
    snippet:   str
    sources:   list[str] = field(default_factory=list)
    urls:      list[str] = field(default_factory=list)
    score:     int = 0
    published: str = ""


def _normalize(text: str) -> set[str]:
    stopwords = {
        "der", "die", "das", "und", "in", "im", "am", "bei", "für", "von",
        "mit", "nach", "an", "zu", "auf", "ist", "ein", "eine", "des",
        "fc", "sc", "sv", "vfb", "the", "a", "of", "at", "for", "to",
        "is", "and", "with", "after", "as", "by", "bvb", "borussia",
        "dortmund", "bayern", "artikel", "laut", "beim",
    }
    tokens = re.findall(r"[a-zäöüß]{3,}", text.lower())
    return {t for t in tokens if t not in stopwords}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _shared_tokens(a: set, b: set) -> int:
    return len(a & b)


def _best_snippet(snippets: list[str]) -> str:
    clean = [s for s in snippets if s and len(s.split()) >= 5]
    if not clean:
        return ""
    return max(clean, key=lambda s: len(s.split()))


def _cluster(items: list, get_title) -> list[list[int]]:
    token_sets = [_normalize(get_title(i)) for i in items]
    clusters:  list[list[int]] = []
    assigned = [False] * len(items)

    for i in range(len(items)):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, len(items)):
            if assigned[j]:
                continue
            jaccard = _jaccard(token_sets[i], token_sets[j])
            shared  = _shared_tokens(token_sets[i], token_sets[j])
            if jaccard >= SIMILARITY_THRESHOLD or shared >= ENTITY_BOOST_WORDS:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)
    return clusters


def rank_news(items: list[NewsItem], top_n: int = 15) -> list[RankedNews]:
    """Clustert NewsItems nach Titel-Aehnlichkeit und gibt RankedNews mit allen Quellen/URLs zurueck."""
    if not items:
        return []

    # URL-Deduplizierung
    seen_urls: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            unique.append(item)

    clusters = _cluster(unique, lambda x: x.title)

    ranked: list[RankedNews] = []
    for cluster in clusters:
        cluster_items = [unique[i] for i in cluster]

        # Quellen + URLs sammeln (dedupliziert nach URL UND Source-Domain)
        seen_urls_cluster:   set[str] = set()
        seen_domains_cluster: set[str] = set()
        all_sources: list[str] = []
        all_urls:    list[str] = []
        for item in cluster_items:
            domain = _domain(item.url)
            if item.url not in seen_urls_cluster and domain not in seen_domains_cluster:
                seen_urls_cluster.add(item.url)
                seen_domains_cluster.add(domain)
                all_sources.append(item.source)
                all_urls.append(item.url)

        best_title = max(cluster_items, key=lambda x: len(x.title)).title
        snippet    = _best_snippet([i.snippet for i in cluster_items])
        dated      = [i for i in cluster_items if i.published]
        pub_str    = max(dated, key=lambda x: x.published).published.strftime("%d.%m.%Y %H:%M") if dated else ""

        ranked.append(RankedNews(
            title=best_title,
            snippet=snippet,
            sources=all_sources[:MAX_SOURCES_PER_NEWS],
            urls=all_urls[:MAX_SOURCES_PER_NEWS],
            score=len(all_urls),
            published=pub_str,
        ))

    ranked.sort(key=lambda x: (x.score, x.published), reverse=True)
    return ranked[:top_n]


def _re_cluster(items: list[RankedNews]) -> list[RankedNews]:
    """Zweites Clustering nach LLM-Enrichment."""
    if not items:
        return []

    clusters = _cluster(items, lambda x: x.title)
    merged: list[RankedNews] = []

    for cluster in clusters:
        cluster_items = [items[i] for i in cluster]

        seen_urls:    set[str] = set()
        seen_domains: set[str] = set()
        all_sources: list[str] = []
        all_urls:    list[str] = []
        for item in cluster_items:
            for src, url in zip(item.sources, item.urls):
                domain = _domain(url)
                if url not in seen_urls and domain not in seen_domains:
                    seen_urls.add(url)
                    seen_domains.add(domain)
                    all_sources.append(src)
                    all_urls.append(url)

        best = max(cluster_items, key=lambda x: len(x.snippet))

        merged.append(RankedNews(
            title=best.title,
            snippet=best.snippet,
            sources=all_sources[:MAX_SOURCES_PER_NEWS],
            urls=all_urls[:MAX_SOURCES_PER_NEWS],
            score=len(all_urls),
            published=max(
                (i.published for i in cluster_items if i.published),
                default=""
            ),
        ))

    merged.sort(key=lambda x: (x.score, x.published), reverse=True)
    return merged


async def enrich_ranked_news(ranked: list[RankedNews], club: str) -> list[RankedNews]:
    from app.services.news_enricher import enrich_news_item

    async def _enrich_one(item: RankedNews) -> RankedNews | None:
        enriched = await enrich_news_item(
            url=item.urls[0] if item.urls else "",
            title=item.title,
            snippet=item.snippet,
            club=club,
        )
        if enriched is None:
            return None
        item.title   = enriched["headline"]
        item.snippet = enriched["snippet"]
        if item.urls:
            item.urls[0] = enriched["url"]
        return item

    results  = await asyncio.gather(*[_enrich_one(r) for r in ranked])
    enriched = [r for r in results if r is not None]

    re_clustered = _re_cluster(enriched)
    logger.info(f"Re-Clustering: {len(enriched)} → {len(re_clustered)} News")
    return re_clustered


def format_news_output(club_name: str, ranked: list[RankedNews]) -> str:
    """Formatiert Top-N News fuer Telegram. Max 3 Quellen pro News, sauber nummeriert."""
    if not ranked:
        return f"⚽ *{club_name}* – Keine aktuellen News gefunden (letzte 48h)."

    top   = ranked[:TOP_N_OUTPUT]
    lines = [f"⚽ *{club_name}* – Top {len(top)} News\n{'─' * 28}"]

    for i, news in enumerate(top):
        medal = MEDALS[i] if i < 3 else f"{i + 1}."
        lines.append(f"\n{medal} *{news.title}*")

        if news.snippet:
            lines.append(news.snippet)

        if news.published:
            lines.append(f"🕐 {news.published}")

        # Nur valide nicht-Google-Redirect URLs anzeigen
        valid_pairs = [
            (url, src) for url, src in zip(news.urls, news.sources)
            if url and url.startswith("http") and "news.google.com" not in url
        ]

        if not valid_pairs:
            pass
        elif len(valid_pairs) == 1:
            url, src = valid_pairs[0]
            lines.append(f"[{src}]({url})")
        else:
            for idx, (url, src) in enumerate(valid_pairs, start=1):
                lines.append(f"{idx}. [{src}]({url})")

    return "\n".join(lines)


def _domain(url: str) -> str:
    try:
        host = url.split("/")[2]
        return host.replace("www.", "")
    except Exception:
        return url
