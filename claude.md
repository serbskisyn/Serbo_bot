# CLAUDE.md — Project Context for Claude Code

## Project Overview
A modular Telegram bot powered by LLMs via OpenRouter. Handles text and voice messages, maintains per-user conversation memory, and protects against prompt injection.

## Architecture

```
app/
├── agents/
│   ├── general_agent.py     # Main message handler + LLM logic
│   ├── chart_agent.py       # Chart generation
│   └── football_agent.py    # Football data queries
├── bot/
│   ├── handlers.py          # All Telegram update handlers (text, voice, commands)
│   ├── conversation.py      # Per-user message history (in-memory)
│   ├── memory.py            # Per-user fact memory (direct + indirect)
│   └── router.py            # Agent routing logic
├── security/
│   └── injection_guard.py   # Two-stage prompt injection detection
├── services/
│   ├── openrouter_client.py # OpenRouter API client (ask_llm, extract_facts)
│   └── speech_to_text.py    # Whisper voice transcription
├── utils/
│   └── logging_setup.py     # Logging configuration
├── config.py                # Env var loading + validation
└── main.py                  # Entry point
```

## Key Design Decisions

- **Language**: Bot responds in German. System prompts are German. Code and comments are German.
- **LLM**: OpenRouter with configurable model (default: `openai/gpt-4o-mini`). Set via `OPENROUTER_MODEL` env var.
- **Memory**: Two-layer memory per user — `direct` (key/value facts) and `indirect` (free-text observations). Extracted async after every message.
- **Injection Guard**: Two-stage — Stage 1 pattern/homoglyph check (instant), Stage 2 LLM-Guard via claude-haiku (only when score > 0). Rate limiting per user (10 msgs / 60s).
- **Routing**: Keyword-based router in `router.py` dispatches to FOOTBALL, CHART or GENERAL agent.
- **Conversation History**: Stored in-memory per user session. Reset via `/reset`.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | required | Telegram bot token |
| `OPENROUTER_API_KEY` | required | OpenRouter API key |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | LLM model identifier |
| `BOT_NAME` | `MeinAgent` | Display name of the bot |
| `LOG_LEVEL` | `INFO` | Logging level |

## Commands

| Command | Handler | Effect |
|---|---|---|
| `/start` | `start_handler` | Greet user, clear history |
| `/reset` | `reset_handler` | Clear conversation history |
| `/memory` | `memory_handler` | Show user memory overview |
| `/forget` | `forget_handler` | Clear user memory |

## Coding Conventions

- All handlers are async — signature `(update: Update, context: ContextTypes.DEFAULT_TYPE)`
- Shared message logic lives in `_process_message()` — do not duplicate between text and voice handlers
- New agents go in `app/agents/` and must be registered in `app/bot/router.py`
- Use `logger = logging.getLogger(__name__)` in every module
- Config values are imported from `app.config` — never use `os.getenv()` directly in other modules
- Security checks use `is_injection_async()` — never the sync wrapper in async contexts

## Adding a New Agent

1. Create `app/agents/your_agent.py` with an async `handle(user_id, text, update)` function
2. Add keywords to `app/bot/router.py` and a new `AgentType` enum value
3. Add routing logic in the `route()` function
4. Import and call the agent in `_process_message()` in `handlers.py`

## Known Limitations / Roadmap

- Memory is in-memory only — lost on restart (SQLite planned)
- No user whitelist / authentication yet
- No tests yet (pytest planned for injection_guard + router)
- No persistent storage yet
- Deploy to server not yet configured
