# Telegram AI Bot

A modular Telegram bot powered by LLMs via [OpenRouter](https://openrouter.ai). Supports multi-agent routing via LangGraph, voice transcription, football data, chart generation, and prompt injection protection.

---

## Overview

\```
You -> Telegram (text or voice) -> Injection Guard -> LangGraph Supervisor -> Agent -> Response
\```

---

## Features

| Status | Feature |
| :--- | :--- |
| ✅ | Telegram bot interface |
| ✅ | LangGraph multi-agent state machine |
| ✅ | Persistent conversation memory (SQLite via AsyncSqliteSaver) |
| ✅ | Multi-agent routing (general, football, chart) |
| ✅ | Voice messages – transcribed via Whisper |
| ✅ | Football Agent – stats, results, standings |
| ✅ | Chart Agent – generates charts on request |
| ✅ | Two-stage prompt injection guard (pattern-based + LLM-Guard) |
| ✅ | Per-user fact memory (confirmed + pending, JSON-persisted) |
| ✅ | OpenRouter integration – any LLM (GPT-4o, Claude, Mistral, ...) |
| ✅ | GitHub Actions CI – runs pytest on every push |

---

## Architecture

```
app/
├── agents/
│   ├── nodes/
│   │   ├── supervisor.py    # Routing node (entry point)
│   │   ├── general.py       # General-purpose LLM agent
│   │   ├── football.py      # Football data agent
│   │   └── chart.py         # Chart generation agent
│   ├── chart_agent.py       # Chart agent logic
│   ├── football_agent.py    # Football agent logic
│   ├── graph.py             # LangGraph StateGraph definition
│   ├── runner.py            # AsyncSqliteSaver + ainvoke runner
│   └── state.py             # BotState TypedDict
├── bot/
│   ├── conversation.py      # Conversation history (JSON-persisted)
│   ├── handlers.py          # Telegram update handlers
│   ├── memory.py            # Per-user fact memory (JSON-persisted)
│   └── router.py            # Keyword-based routing logic
├── security/
│   └── injection_guard.py   # Two-stage prompt injection detection
├── services/
│   ├── openrouter_client.py # OpenRouter API client
│   └── speech_to_text.py    # Voice message transcription
├── utils/
│   └── logging_setup.py     # Logging configuration
├── data/
│   ├── memory.json          # Per-user fact memory (auto-generated)
│   └── conversation.db      # LangGraph SQLite checkpoint (auto-generated)
├── config.py                # Environment config loader
└── main.py                  # Entry point
```


### Stack

- **LangGraph** – multi-agent state machine with AsyncSqliteSaver
- **OpenRouter** – LLM backbone (model configurable via `.env`)
- **python-telegram-bot** – Telegram interface
- **Whisper** – local voice transcription
- **aiosqlite** – async SQLite for persistent conversation memory
- Python 3.11+

---

## Setup

### Prerequisites

- Python 3.11+
- Telegram bot token (via [@BotFather](https://t.me/BotFather))
- [OpenRouter](https://openrouter.ai) API key
- ffmpeg (for voice transcription)

### Installation

\```bash
git clone https://github.com/serbskisyn/Serbo_bot.git
cd Serbo_bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg
\```

### Configuration

\```bash
cp .env.example .env  # fill in API keys
\```

| Variable | Description |
| :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `OPENROUTER_API_KEY` | API key from openrouter.ai |
| `OPENROUTER_MODEL` | Model ID, e.g. `openai/gpt-4o` |

### Run

\```bash
python -m app.main
pytest tests/ -v
\```

---

## Usage

| Message | Routed to |
| :--- | :--- |
| "Erkläre mir Quantencomputing" | `general_node` |
| "Wer hat gestern für Bayern getroffen?" | `football_node` |
| "Zeig mir ein Balkendiagramm" | `chart_node` |
| 🎤 Voice note | Whisper -> any agent |

**Commands:**

`/start` `/reset` `/memory` `/forget`

---

## Security

Every incoming message passes through a two-stage injection guard before reaching any agent:

- **Stage 1** – Pattern-based hard block + soft score (free, instant)
- **Stage 2** – LLM-Guard via OpenRouter (only when score > 0)

---

## Adding a New Agent

1. Create `app/agents/nodes/your_agent.py` with an async node function
2. Create `app/agents/your_agent.py` with the agent logic
3. Register the node in `app/agents/graph.py`
4. Add keyword routing in `app/bot/router.py`

---

## Roadmap

- [x] LangGraph multi-agent architecture
- [x] Persistent conversation memory (SQLite)
- [x] GitHub Actions CI- [ ] Football News Summary with fact check and quality score
- [ ] Web search integration
- [ ] User whitelist / authentication
- [ ] Dienstplan Agent (3-shift scheduling with rule validation)
- [ ] Control Agent (validates Dienstplan against rules + holidays)
- [ ] Google Sheets integration (staff data, vacation, sick leave)
- [ ] Deploy to server (Railway / Fly.io)

---

## License

Private project – not licensed for public use.
