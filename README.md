# Telegram AI Bot

A modular Telegram bot powered by LLMs via [OpenRouter](https://openrouter.ai). Supports multi-agent routing via LangGraph, voice transcription, football data, chart generation, web search, and prompt injection protection.

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
| ✅ | Multi-agent routing (general, football, chart, web) |
| ✅ | LLM-based supervisor routing (no keyword matching) |
| ✅ | Voice messages – transcribed via Whisper |
| ✅ | Football Agent – stats, results, standings |
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

    app/
    ├── agents/
    │   ├── nodes/
    │   │   ├── supervisor.py    # LLM-based routing node (entry point)
    │   │   ├── general.py       # General-purpose LLM agent
    │   │   ├── football.py      # Football data agent
    │   │   ├── chart.py         # Chart generation agent (renders PNG)
    │   │   └── web.py           # Web search agent (Tavily)
    │   ├── chart_agent.py       # Chart code generation + execution + PNG export
    │   ├── football_agent.py    # Football LLM logic
    │   ├── graph.py             # LangGraph StateGraph definition
    │   ├── runner.py            # AsyncSqliteSaver + ainvoke runner
    │   └── state.py             # BotState TypedDict
    ├── bot/
    │   ├── handlers.py          # Telegram update handlers
    │   ├── router.py            # Keyword-based routing (legacy, unused)
    │   ├── whitelist.py         # User whitelist check
    │   ├── conversation.py      # In-memory conversation history
    │   └── memory.py            # Per-user fact memory (JSON-persisted)
    ├── security/
    │   ├── injection_guard.py   # Two-stage prompt injection detection (async)
    │   └── rate_limiter.py      # Sliding window rate limiter per user
    ├── services/
    │   ├── openrouter_client.py # OpenRouter API client + fact extractor
    │   ├── speech_to_text.py    # Voice message transcription (Whisper)
    │   └── web_search.py        # Tavily search client
    ├── utils/
    │   └── logging_setup.py     # Logging configuration
    ├── data/
    │   ├── memory.json          # Per-user fact memory (auto-generated)
    │   └── conversation.db      # LangGraph SQLite checkpoint (auto-generated)
    ├── config.py                # Environment config loader
    └── main.py                  # Entry point

### Stack

- **LangGraph** – multi-agent state machine with AsyncSqliteSaver
- **OpenRouter** – LLM backbone (model configurable via `.env`)
- **python-telegram-bot** – Telegram interface
- **Whisper** – local voice transcription
- **Tavily** – web search for live information
- **matplotlib** – chart rendering to PNG
- **aiosqlite** – async SQLite for persistent conversation memory
- Python 3.11+

---

## Setup

### Prerequisites

- Python 3.11+
- Telegram bot token (via [@BotFather](https://t.me/BotFather))
- [OpenRouter](https://openrouter.ai) API key
- [Tavily](https://tavily.com) API key (free tier: 1.000 searches/month)
- ffmpeg (for voice transcription)

### Installation

    git clone https://github.com/serbskisyn/Serbo_bot.git
    cd Serbo_bot
    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    brew install ffmpeg

### Configuration

    cp .env.example .env  # fill in API keys

| Variable | Description |
| :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `OPENROUTER_API_KEY` | API key from openrouter.ai |
| `OPENROUTER_MODEL` | Model ID, e.g. `openai/gpt-4o-mini` |
| `TAVILY_API_KEY` | API key from tavily.com |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs, e.g. `123456789,987654321` |
| `RATE_LIMIT_MAX_REQUESTS` | Max messages per window (default: `10`) |
| `RATE_LIMIT_WINDOW_SECONDS` | Window size in seconds (default: `60`) |

### Run

    python -m app.main
    pytest tests/ -v

---

## Usage

| Message | Routed to |
| :--- | :--- |
| „Erkläre mir Quantencomputing" | `general_node` |
| „Was weißt du zu Niklas Süle?" | `football_node` |
| „Zeig mir ein Balkendiagramm" | `chart_node` |
| „Was sind heute die aktuellen Nachrichten?" | `web_node` |
| 🎤 Voice note | Whisper -> any agent |

**Commands:**

`/start` `/reset` `/memory` `/forget`

---

## Security

Every incoming message passes through three layers before reaching any agent:

- **Whitelist** – only allowed Telegram user IDs can interact with the bot
- **Rate Limiter** – sliding window per user, configurable via `.env`
- **Stage 1** – pattern-based hard block + soft score (free, instant)
- **Stage 2** – LLM-Guard via OpenRouter (fully async, only when score > 0)

---

## Adding a New Agent

1. Create `app/agents/nodes/your_agent.py` with an async node function
2. Create `app/agents/your_agent.py` with the agent logic
3. Register the node in `app/agents/graph.py`
4. Add routing description in `app/agents/nodes/supervisor.py` ROUTING_PROMPT

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
- [ ] Football News Summary with fact check and quality score
- [ ] Morning Briefing – daily summary via Telegram
- [ ] Dienstplan Agent (3-shift scheduling with rule validation)
- [ ] Control Agent (validates Dienstplan against rules + holidays)
- [ ] Google Sheets integration (staff data, vacation, sick leave)
- [ ] Deploy to server (Railway / Fly.io)

---

## License

Private project – not licensed for public use.
