# Telegram AI Bot

A modular Telegram bot powered by LLMs via [OpenRouter](https://openrouter.ai). Supports multi-agent routing, voice transcription, football data, chart generation, and prompt injection protection.

---

## Overview

```
You -> Telegram (text or voice) -> Injection Guard -> Router -> Agent -> Response
```

---

## Features

| Status | Feature |
| :--- | :--- |
| ✅ | Telegram bot interface |
| ✅ | Multi-agent routing (general, football, chart) |
| ✅ | Voice messages - transcribed via Whisper |
| ✅ | Conversation memory - per-user session history |
| ✅ | Football Agent - stats, results, standings |
| ✅ | Chart Agent - generates charts on request |
| ✅ | Prompt injection guard - pattern-based detection |
| ✅ | OpenRouter integration - any LLM (GPT-4o, Claude, Mistral, ...) |

---

## Architecture

```
app/
├── agents/
│   ├── chart_agent.py       # Chart generation agent
│   ├── football_agent.py    # Football data agent
│   └── general_agent.py     # General-purpose LLM agent
├── bot/
│   ├── conversation.py      # Conversation state management
│   ├── handlers.py          # Telegram update handlers
│   ├── memory.py            # Per-user message memory
│   └── router.py            # Agent routing logic
├── security/
│   └── injection_guard.py   # Prompt injection detection
├── services/
│   ├── openrouter_client.py # OpenRouter API client
│   └── speech_to_text.py    # Voice message transcription
├── utils/
│   └── logging_setup.py     # Logging configuration
├── config.py                # Environment config loader
└── main.py                  # Entry point
```

### Stack

- **OpenRouter** - LLM backbone (model configurable via `.env`)
- **python-telegram-bot** - Telegram interface
- **Whisper** - voice transcription
- Python 3.10+

---

## Setup

### Prerequisites

- Python 3.10+
- Telegram bot token (via [@BotFather](https://t.me/BotFather))
- [OpenRouter](https://openrouter.ai) API key
- ffmpeg (for voice transcription)

### Installation

```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env  # fill in API keys
```

| Variable | Description |
| :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `OPENROUTER_API_KEY` | API key from openrouter.ai |
| `OPENROUTER_MODEL` | Model ID, e.g. `openai/gpt-4o` |

### Run

```bash
python -m app.main
```

---

## Usage

| Message | Routed to |
| :--- | :--- |
| "Explain quantum computing" | `general_agent` |
| "Who scored last night for Bayern?" | `football_agent` |
| "Show me a bar chart of my data" | `chart_agent` |
| Voice note | Whisper -> any agent |

---

## Security

The `injection_guard.py` module scans every incoming message for known prompt injection patterns before routing. Flagged messages are blocked and never reach the LLM.

---

## Adding a New Agent

1. Create `app/agents/your_agent.py` with a handler function.
2. Register the agent in `app/bot/router.py` with routing logic.

---

## Roadmap

- [ ] Web search integration
- [ ] User whitelist / authentication
- [ ] Deploy to server (Railway / Fly.io)
- [ ] Persistent memory across restarts

---

## License

MIT License - see [LICENSE](LICENSE) for details.
