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