# 🤖 Telegram AI Bot

A modular, production-ready Telegram bot powered by LLMs via [OpenRouter](https://openrouter.ai). The bot supports multi-agent routing, voice message transcription, memory-aware conversations, football data, chart generation, and prompt injection protection.

---

## Features

- 🧠 **Multi-Agent Architecture** — Automatically routes messages to the right agent (general, football, chart)
- 🎙️ **Speech-to-Text** — Transcribes voice messages via Whisper API
- 💬 **Conversation Memory** — Maintains per-user message history within a session
- ⚽ **Football Agent** — Answers football-related queries (stats, results, standings)
- 📊 **Chart Agent** — Generates charts and visualizations on request
- 🔒 **Injection Guard** — Detects and blocks prompt injection attempts
- 🔧 **OpenRouter Integration** — Supports any LLM available on OpenRouter (GPT-4o, Claude, Mistral, etc.)

---

## Project Structure

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

---

## Requirements

- Python 3.10+
- A Telegram Bot Token (via [@BotFather](https://t.me/BotFather))
- An [OpenRouter](https://openrouter.ai) API key

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_MODEL=openai/gpt-4o
```

### 5. Run the bot

```bash
python -m app.main
```

---

## Configuration

| Variable | Description | Required |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token from BotFather | ✅ |
| `OPENROUTER_API_KEY` | API key from openrouter.ai | ✅ |
| `OPENROUTER_MODEL` | Model identifier (e.g. `openai/gpt-4o`) | ✅ |

---

## How It Works

1. A user sends a message (text or voice) to the bot on Telegram.
2. **Voice messages** are transcribed to text via the speech-to-text service.
3. The **router** analyzes the message and selects the appropriate agent.
4. The selected **agent** processes the message using the configured LLM via OpenRouter.
5. The **injection guard** runs on every incoming message to detect malicious prompts.
6. The response is sent back to the user with conversation **memory** maintained per session.

---

## Security

The `injection_guard.py` module scans incoming messages for common prompt injection patterns and refuses to process flagged input. This helps prevent users from manipulating the bot's system prompt or bypassing its intended behavior.

---

## Development

### Logging

Logging is configured in `app/utils/logging_setup.py`. Adjust the log level via the `LOG_LEVEL` environment variable (default: `INFO`).

### Adding a New Agent

1. Create `app/agents/your_agent.py` with a handler function.
2. Register the agent in `app/bot/router.py` with appropriate routing logic.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.
