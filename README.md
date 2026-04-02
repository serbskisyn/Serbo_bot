# ðŸ¤– Telegram AI Bot

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
| âœ… | Telegram bot interface |
| âœ… | Multi-agent routing (general, football, chart) |
| âœ… | Voice messages â€“ transcribed via Whisper |
| âœ… | Conversation memory â€“ per-user session history |
| âœ… | Football Agent â€“ stats, results, standings |
| âœ… | Chart Agent â€“ generates charts on request |
| âœ… | Prompt injection guard â€“ pattern-based detection |
| âœ… | OpenRouter integration â€“ any LLM (GPT-4o, Claude, Mistral, â€¦) |

---

## Architecture

```
app/
â”œâ”€â”€ agents/
â”‚   â”œâ”€â”€ chart_agent.py       # Chart generation agent
â”‚   â”œâ”€â”€ football_agent.py    # Football data agent
â”‚   â””â”€â”€ general_agent.py     # General-purpose LLM agent
â”œâ”€â”€ bot/
â”‚   â”œâ”€â”€ conversation.py      # Conversation state management
â”‚   â”œâ”€â”€ handlers.py          # Telegram update handlers
â”‚   â”œâ”€â”€ memory.py            # Per-user message memory
â”‚   â””â”€â”€ router.py            # Agent routing logic
â”œâ”€â”€ security/
â”‚   â””â”€â”€ injection_guard.py   # Prompt injection detection
â”œâ”€â”€ services/
â”‚   â”œâ”€â”€ openrouter_client.py # OpenRouter API client
â”‚   â””â”€â”€ speech_to_text.py    # Voice message transcription
â”œâ”€â”€ utils/
â”‚   â””â”€â”€ logging_setup.py     # Logging configuration
â”œâ”€â”€ config.py                # Environment config loader
â””â”€â”€ main.py                  # Entry point
```

### Stack

- **OpenRouter** â€“ LLM backbone (model configurable via `.env`)
- **python-telegram-bot** â€“ Telegram interface
- **Whisper** â€“ voice transcription
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
| ðŸŽ¤ Voice note | Whisper â†’ any agent |

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

MIT License â€“ see [LICENSE](LICENSE) for details.