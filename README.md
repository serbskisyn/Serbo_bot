# Telegram AI Bot

A modular Telegram bot powered by LLMs via OpenRouter. Supports multi-agent routing via LangGraph, voice transcription, football news aggregation, chart generation, web search, and prompt injection protection.

---

## Overview

You -> Telegram (text or voice) -> Whitelist -> Rate Limiter -> Injection Guard -> LangGraph Supervisor (LLM) -> Agent -> Response

---

## Features

| Status | Feature |
| :--- | :--- |
| ✅ | Telegram bot interface |
| ✅ | LangGraph multi-agent state machine |
| ✅ | Persistent conversation memory (SQLite via AsyncSqliteSaver) |
| ✅ | LLM-based supervisor routing (no keyword matching) |
| ✅ | Voice messages – transcribed via Whisper |
| ✅ | General Agent – LLM-powered assistant |
| ✅ | Football Agent – stats, results, standings |
| ✅ | Football News Agent – /news command with multi-source aggregation |
| ✅ | News Ranking – Jaccard-clustering + source scoring + re-clustering after LLM enrichment |
| ✅ | News Enrichment – LLM generates German headlines + 50-word snippets |
| ✅ | Club Memory Integration – favourite clubs auto-loaded from user memory |
| ✅ | Chart Agent – renders and sends PNG via Telegram |
| ✅ | Web Agent – live web search via Tavily |
| ✅ | Two-stage prompt injection guard (pattern-based + LLM-Guard, fully async) |
| ✅ | Rate limiting – sliding window per user (configurable) |
| ✅ | User whitelist – only allowed Telegram users can access the bot |
| ✅ | Per-user fact memory (confirmed + pending, JSON-persisted) |
| ✅ | OpenRouter integration – any LLM (GPT-4o, Claude, Mistral, ...) |
| ✅ | GitHub Actions CI – runs pytest on every push |

---

## Architecture

```
app/
├── agents/
│ ├── nodes/
│ │ ├── supervisor.py # LLM-based routing node (entry point)
│ │ ├── general.py # General-purpose LLM agent
│ │ ├── football.py # Football data agent
│ │ ├── chart.py # Chart generation agent (renders PNG)
│ │ └── web.py # Web search agent (Tavily)
│ ├── chart_agent.py # Chart code generation + execution + PNG export
│ ├── football_agent.py # Football LLM logic
│ ├── football_news_agent.py # /news orchestrator — Memory + Fetch + Rank + Enrich
│ ├── graph.py # LangGraph StateGraph definition
│ ├── runner.py # AsyncSqliteSaver + ainvoke runner
│ └── state.py # BotState TypedDict
├── bot/
│ ├── handlers.py # Telegram update handlers + /news command
│ ├── whitelist.py # User whitelist check
│ ├── conversation.py # In-memory conversation history
│ └── memory.py # Per-user fact memory (JSON-persisted)
├── security/
│ ├── injection_guard.py # Two-stage prompt injection detection (async)
│ └── rate_limiter.py # Sliding window rate limiter per user
├── services/
│ ├── news_fetcher.py # GNews API + Google News RSS + club-specific feeds
│ ├── news_ranker.py # Jaccard-clustering, source scoring, re-clustering
│ ├── news_enricher.py # LLM-based German headlines + snippets
│ ├── openrouter_client.py # OpenRouter API client + fact extractor
│ ├── speech_to_text.py # Voice message transcription (Whisper)
│ └── web_search.py # Tavily search client
├── utils/
│ └── logging_setup.py # Logging configuration
├── data/
│ ├── memory.json # Per-user fact memory (auto-generated)
│ └── conversation.db # LangGraph SQLite checkpoint (auto-generated)
├── config.py # Environment config loader
└── main.py # Entry point
```

### Stack

- LangGraph – multi-agent state machine with AsyncSqliteSaver
- OpenRouter – LLM backbone (model configurable via .env)
- GNews API – primary news source with full snippets
- Google News RSS – secondary news layer (club-specific queries)
- Transfermarkt RSS – club-specific transfer news
- python-telegram-bot – Telegram interface
- Whisper – local voice transcription
- Tavily – web search for live information
- matplotlib – chart rendering to PNG
- aiosqlite – async SQLite for persistent conversation memory
- Python 3.11+

---

## Setup

### Prerequisites

- Python 3.11+
- Telegram bot token (via @BotFather)
- OpenRouter API key
- Tavily API key (free tier: 1.000 searches/month)
- GNews API key (free tier: 100 requests/day)
- ffmpeg (for voice transcription)

### Installation

git clone https://github.com/serbskisyn/Serbo_bot.git
cd Serbo_bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg

### Configuration

cp .env.example .env

| Variable | Description |
| :--- | :--- |
| TELEGRAM_BOT_TOKEN | Bot token from BotFather |
| OPENROUTER_API_KEY | API key from openrouter.ai |
| OPENROUTER_MODEL | Model ID, e.g. openai/gpt-4o-mini |
| TAVILY_API_KEY | API key from tavily.com |
| GNEWS_API_KEY | API key from gnews.io |
| ALLOWED_USER_IDS | Comma-separated Telegram user IDs, e.g. 123456789,987654321 |
| RATE_LIMIT_MAX_REQUESTS | Max messages per window (default: 10) |
| RATE_LIMIT_WINDOW_SECONDS | Window size in seconds (default: 60) |

### Run

python -m app.main
pytest tests/ -v

---

## Usage

| Message | Routed to |
| :--- | :--- |
| "Erklaere mir Quantencomputing" | general_node |
| "Was weisst du zu Niklas Sule?" | football_node |
| "Zeig mir ein Balkendiagramm" | chart_node |
| "Was sind heute die aktuellen Nachrichten?" | web_node |
| /news | Football News Agent – top 10 news per favourite club |
| Voice note | Whisper -> any agent |

Commands:

/start /reset /memory /forget /news

---

## News Agent

The /news command fetches and ranks the latest news for your favourite clubs stored in memory.

Pipeline:

Memory (favourite clubs)
-> GNews API (primary, with snippets)
-> Google News RSS (club-specific queries)
-> Static RSS Feeds (Sportbild, Bild Sport, Sky Sports)
-> Club-specific Feeds (Transfermarkt, club websites, regional media)
-> Jaccard Clustering (group similar articles)
-> Source Scoring (more sources = higher priority)
-> LLM Enrichment (German headline + 50-word snippet per article)
-> Re-Clustering (merge duplicates after LLM translation)
-> Top 10 output per club

Adding a new club's feeds:

Add an entry to CLUB_FEEDS in app/services/news_fetcher.py:

"fc schalke 04": [
"https://www.transfermarkt.de/fc-schalke-04/rss/verein/33",
"https://www.reviersport.de/rss.xml",
],

---

## Security

Every incoming message passes through three layers before reaching any agent:

- Whitelist – only allowed Telegram user IDs can interact with the bot
- Rate Limiter – sliding window per user, configurable via .env
- Stage 1 – pattern-based hard block + soft score (free, instant)
- Stage 2 – LLM-Guard via OpenRouter (fully async, only when score > 0)

---

## Adding a New Agent

1. Create app/agents/nodes/your_agent.py with an async node function
2. Create app/agents/your_agent.py with the agent logic
3. Register the node in app/agents/graph.py
4. Add routing keyword in app/agents/nodes/supervisor.py ROUTING_PROMPT

---

## Roadmap

- [x] LangGraph multi-agent architecture
- [x] Persistent conversation memory (SQLite)
- [x] Rate limiting – sliding window per user
- [x] Web search integration (Tavily)
- [x] User whitelist / authentication
- [x] GitHub Actions CI
- [x] LLM-based supervisor routing
- [x] Chart Agent – renders and sends PNG via Telegram
- [x] Per-user fact memory (confirmed + pending)
- [x] Football News Agent (/news command)
- [x] Multi-source news aggregation (GNews + RSS + club feeds)
- [x] LLM news enrichment (German headlines + snippets)
- [x] Jaccard clustering + source ranking
- [] Daily news briefing (scheduled via JobQueue)
- [] Football News fact-check + quality score
- [] Dienstplan Agent (3-shift scheduling with rule validation)
- [] Control Agent (validates Dienstplan against rules + holidays)
- [] Google Sheets integration (staff data, vacation, sick leave)
- [] Deploy to server (Railway / Fly.io)

---

## License

Private project – not licensed for public use.