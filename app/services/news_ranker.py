import re
import logging
from dataclasses import dataclass, field
from app.services.news_fetcher import NewsItem

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.35  # ab wann zwei Titel als "gleiche Meldung" gelten


@dataclass
class RankedNews:
    title: str           # repräsentativer Titel (längster / meiste Quellen)
    snippet: str         # bestes Snippet
    sources: list[str]   # alle Quellen die diese Meldung hatten
    urls: list[str]      # alle URLs (eine pro Quelle)
    score: int           # Anzahl Quellen = Priorität
    published: str = ""  # Datum des neuesten Artikels


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _normalize(text: str) -> set[str]:
    """Tokenisiert + stopword-filtert einen Titel für Vergleich."""
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
    """Wählt das längste nicht-leere Snippet."""
    return max(snippets, key=len) if snippets else ""


def _format_date(item: "NewsItem") -> str:
    if item.published is None:
        return ""
    return item.published.strftime("%d.%m.%Y %H:%M")


def _display_url(url: str) -> str:
    """
    Gibt einen lesbaren Anzeigenamen für eine URL zurück.
    Beispiel: https://www.bild.de/sport/... → bild.de
    """
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        # www. entfernen
        host = re.sub(r"^www\.", "", host)
        return host
    except Exception:
        return url


# ── Kern-Logik ────────────────────────────────────────────────────────────────

def rank_news(items: list[NewsItem], top_n: int = 10) -> list[RankedNews]:
    """
    Gruppiert ähnliche Meldungen, bewertet nach Quellenanzahl
    und gibt Top-N zurück.
    """
    if not items:
        return []

    # Duplikate via URL entfernen
    seen_urls: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            unique.append(item)

    # Tokenisierte Titel vorberechnen
    token_sets = [_normalize(item.title) for item in unique]

    # Cluster bilden
    clusters: list[list[int]] = []
    assigned = [False] * len(unique)

    for i in range(len(unique)):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, len(unique)):
            if assigned[j]:
                continue
            sim = _jaccard(token_sets[i], token_sets[j])
            if sim >= SIMILARITY_THRESHOLD:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)

    # Cluster → RankedNews
    ranked: list[RankedNews] = []
    for cluster in clusters:
        cluster_items = [unique[i] for i in cluster]

        best_title = max(cluster_items, key=lambda x: len(x.title)).title

        source_map: dict[str, str] = {}
        for item in cluster_items:
            if item.source not in source_map:
                source_map[item.source] = item.url

        sources = list(source_map.keys())
        urls    = list(source_map.values())

        snippet = _best_snippet([item.snippet for item in cluster_items if item.snippet])

        dated = [item for item in cluster_items if item.published]
        pub_str = _format_date(max(dated, key=lambda x: x.published)) if dated else ""

        ranked.append(RankedNews(
            title=best_title,
            snippet=snippet,
            sources=sources,
            urls=urls,
            score=len(sources),
            published=pub_str,
        ))

    ranked.sort(key=lambda x: (x.score, x.published), reverse=True)
    return ranked[:top_n]


# ── Formatter ─────────────────────────────────────────────────────────────────

MEDALS = ["🥇", "🥈", "🥉"]


def format_news_output(club_name: str, ranked: list[RankedNews]) -> str:
    if not ranked:
        return f"⚽ *{club_name}* – Keine aktuellen News gefunden (letzte 48h)."

    lines = [f"⚽ *{club_name}* – Top News\n{'─' * 30}"]

    for i, news in enumerate(ranked):
        medal = MEDALS[i] if i < 3 else f"{i + 1}."
        source_count = f"[{news.score} {'Quelle' if news.score == 1 else 'Quellen'}]"

        lines.append(f"\n{medal} {source_count} *{news.title}*")

        # Snippet – HTML-Tags entfernen, max 500 Zeichen
        if news.snippet:
            snippet = re.sub(r"<[^>]+>", "", news.snippet)[:500].strip()
            if len(re.sub(r"<[^>]+>", "", news.snippet)) > 500:
                snippet += "…"
            lines.append(f"_{snippet}_")

        if news.published:
            lines.append(f"🕐 {news.published}")

        # Quellen mit lesbaren Domain-Namen statt kryptischer URLs
        source_refs = " · ".join(
            f"[{_display_url(url)}]({url})"
            for url in news.urls
        )
        lines.append(source_refs)

    return "\n".join(lines)
