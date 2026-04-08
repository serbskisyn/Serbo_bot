ranked   = rank_news(result, top_n=10)
enriched = await enrich_ranked_news(ranked, club)
block    = format_news_output(club, enriched)