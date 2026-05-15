<h1 align="center">Serbo Bot</h1>

<p align="center">
  <strong>Production-grade Telegram AI Assistant + Unified Trading Platform</strong><br>
  <sub>Running on Raspberry Pi · Powered by LLMs · Multi-Agent · Always On</sub>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python" />
  <img src="https://img.shields.io/badge/Platform-Raspberry%20Pi-red?style=flat-square&logo=raspberrypi" />
  <img src="https://img.shields.io/badge/LLM-OpenRouter-orange?style=flat-square" />
  <img src="https://img.shields.io/badge/Exchanges-Kraken%20%2B%20Alpaca-green?style=flat-square" />
  <img src="https://img.shields.io/badge/License-Private-lightgrey?style=flat-square" />
</p>

---

Serbo Bot is a modular, self-hosted Telegram bot that combines a full **multi-agent AI assistant** with an autonomous **trading platform** for crypto (Kraken) and US stocks (Alpaca). It runs 24/7 on a Raspberry Pi as three independent systemd services — bot, trade engine, and Freqtrade — each with its own lifecycle.

```python
# Ask anything in Telegram — routed to the right agent automatically
"Was läuft gerade bei Borussia Dortmund?"   → Football Agent (Tavily live data)
"Zeig mir ein Chart von SPY vs QQQ"         → Chart Agent (matplotlib → PNG)
"Wie ist die aktuelle Marktlage?"           → Web Agent (Tavily search)

# Or trade directly
/stocks        → Alpaca account + open positions
/tradebot      → Kraken crypto status
/stocks scan   → Trigger LLM scan across 15 US symbols
```

---

## Features

### AI Assistant
| | Feature |
| :--- | :--- |
| ✅ | LangGraph multi-agent state machine — Supervisor routes to 4 specialised agents |
| ✅ | LLM-based routing with confidence scoring + topic-carry for follow-up questions |
| ✅ | Football safety net — club name + football term always routes to Football Agent |
| ✅ | General Agent — concise assistant with per-user fact memory |
| ✅ | Football Agent — live stats via Tavily on demand |
| ✅ | Chart Agent — LLM generates matplotlib code → PNG sent via Telegram |
| ✅ | Web Agent — Tavily search → German summary with sources |
| ✅ | Voice messages — transcribed locally via Whisper (lazy-loaded) |
| ✅ | Per-user fact memory — confirmed (direct) + pending (indirect, threshold 5) |
| ✅ | Persistent conversation history — SQLite via AsyncSqliteSaver, keyed by user |
| ✅ | Two-stage prompt injection guard — pattern/homoglyph + LLM-Guard |
| ✅ | Rate limiting — sliding window per user |
| ✅ | User whitelist — only allowed Telegram IDs |

### Trading Platform
| | Feature |
| :--- | :--- |
| ✅ | **Trade Engine** — unified service for Crypto + US stocks (Port 8081 REST API) |
| ✅ | **Kraken Crypto** — 15 BTC pairs, LLM signals on 5m candles, 24/7 |
| ✅ | **Alpaca US Stocks** — 15 symbols, LLM signals on 5m candles, Mo–Fr market hours |
| ✅ | LLM strategy — RSI / EMA20/50 / Bollinger Bands + confidence threshold (0.65) |
| ✅ | Sentiment filter — Fear & Greed, Polymarket Gamma API, Tavily news per symbol |
| ✅ | EMA50 trend filter — blocks entries when trend is down (reduces false signals) |
| ✅ | Price Monitor — 30s stop-loss + trailing stop checks (no LLM, just price) |
| ✅ | Stop-loss 2% / Trailing stop: activates at +2%, trails at 1% |
| ✅ | SQLite position tracking — full trade log with P&L history |
| ✅ | Telegram push alerts — every trade + stop trigger sent instantly |
| ✅ | Freqtrade (legacy) — LLM crypto strategy still running in parallel |

### Productivity & Automation
| | Feature |
| :--- | :--- |
| ✅ | Football News (`/news`) — 4-layer aggregation, LLM enrichment, SQLite cache |
| ✅ | Daily news push — scheduled briefing via JobQueue |
| ✅ | Shift scheduler (`/dienstplan`) — 3-shift nursing schedule, 8 hard + 3 soft constraints |
| ✅ | Google Sheets integration — staff, vacation, sick leave, output |
| ✅ | Google Calendar — reminders + daily overview |
| ✅ | Strava Kudos (`/strava`) — session-cookie automation, auto-likes feed |
| ✅ | `/claude` — Claude Code CLI (text-only) |
| ✅ | `/claudex` — Claude Agent with full tool access (read/write/git/bash) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Telegram (User)                       │
└────────────────────────┬────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │     serbo_bot       │  systemd service
              │  (python-telegram-  │  Port: —
              │       bot)          │
              └──────────┬──────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
    Whitelist      Rate Limiter    Injection Guard
    (user IDs)    (sliding window)  (pattern + LLM)
         │               │               │
         └───────────────┼───────────────┘
                         │
              ┌──────────▼──────────┐
              │   LangGraph Runner  │
              │  (AsyncSqliteSaver) │
              └──────────┬──────────┘
                         │
                  Supervisor Node
               (LLM routing, conf≥0.6)
                         │
        ┌────────┬───────┼───────┬────────┐
        │        │       │       │        │
    General  Football  Chart   Web    /commands
     Node     Node    Node    Node
  (mem+LLM) (Tavily) (mpl)  (Tavily)
        │
        └─── /tradebot ──► Trade Engine API (127.0.0.1:8081)
        └─── /stocks   ──► Trade Engine API (127.0.0.1:8081)

┌─────────────────────────────────────────────────────────┐
│               trade_engine              systemd service  │
│                                         Port: 8081       │
│                                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │ Crypto Loop │  │ Stocks Loop │  │ Price Monitor  │  │
│  │  (5 Min)    │  │  (5 Min)    │  │   (30 Sek)     │  │
│  │  24/7       │  │  Mo–Fr ET   │  │  Stop-Loss     │  │
│  └──────┬──────┘  └──────┬──────┘  └───────┬────────┘  │
│         │                │                  │            │
│         └────────────────┼──────────────────┘            │
│                          │                               │
│                   ┌──────▼──────┐                        │
│                   │   Scanner   │                        │
│                   │ EMA50 Filter│                        │
│                   │  Sentiment  │                        │
│                   │  LLM Signal │                        │
│                   └──────┬──────┘                        │
│                          │                               │
│            ┌─────────────┼─────────────┐                │
│            │             │             │                 │
│     KrakenExchange  AlpacaExchange  TradeManager        │
│     (ccxt, 15 BTC)  (alpaca-py,     (SQLite,            │
│                      15 symbols)    Stop/Trail)          │
└─────────────────────────────────────────────────────────┘
```

---

## Telegram Commands

### AI Assistant
| Command | Description |
| :--- | :--- |
| `/start` | Welcome + full command list |
| `/reset` | Clear conversation history |
| `/memory` | Show confirmed + pending facts |
| `/forget` | Wipe all memory |

### Trading
| Command | Description |
| :--- | :--- |
| `/tradebot` | Crypto status — open positions, P&L, stats |
| `/tradebot scan` | Trigger manual crypto scan |
| `/stocks` | Alpaca account + open positions |
| `/stocks scan` | Trigger manual US-stock scan |
| `/stocks help` | Command overview |

### News & Calendar
| Command | Description |
| :--- | :--- |
| `/news` | Latest news for your favourite clubs |
| `/news fresh` | Force live re-fetch (bypass cache) |
| `/termine` | Next Google Calendar events |
| `/kalender1` / `/kalender2` | Specific calendar overview |
| `/health` / `/check` | System health check |

### Automation
| Command | Description |
| :--- | :--- |
| `/strava` | Give Kudos to all Strava feed activities |
| `/dienstplan` | Interactive 3-shift schedule builder |
| `/claude <prompt>` | Claude Code CLI — text only |
| `/claudex <task>` | Claude Agent — full tool access (files, git, bash) |

---

## Trade Engine

The Trade Engine is a standalone service (`/home/pi/trade_engine/`) that runs independently of the Telegram bot. It manages all trading logic — the bot is only a frontend.

### Strategy Logic

```
For each symbol every 5 minutes:

1. Fetch 100× 5m candles (Kraken or Alpaca)
2. Calculate indicators: RSI · EMA20 · EMA50 · Bollinger Bands
3. EMA50 slope filter → skip if trending down
4. Sentiment block → skip if extreme fear/greed or high macro risk
5. Call LLM (OpenRouter) → {"signal": "buy|sell|hold", "confidence": 0.0–1.0}
6. Execute if confidence ≥ 0.65

Every 30 seconds (Price Monitor, no LLM):
→ Check stop-loss (−2%) and trailing stop (+2% activate, 1% trail)
→ Close position immediately if triggered
```

### Watchlist

**Crypto (Kraken, 15 pairs):**
`ETH · SOL · XRP · ADA · LTC · LINK · DOT · ATOM · DOGE · XLM · UNI · AAVE · ETC · TRX · XMR`
all vs BTC · 24/7

**US Stocks (Alpaca, 15 symbols):**
`SPY · QQQ · GLD · AAPL · MSFT · NVDA · TSLA · XLF · USO · AMZN · GOOGL · META · AMD · JPM · IWM`
Mo–Fr 10:00–15:45 ET

### REST API (Port 8081)

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/health` | GET | Liveness check |
| `/status` | GET | Full status — positions, account, stats |
| `/positions?market=crypto\|stocks` | GET | Open positions |
| `/stats` | GET | Trade history — wins, losses, P&L |
| `/scan?market=all\|crypto\|stocks` | POST | Trigger manual scan |

All endpoints require `X-API-Secret` header.

---

## Setup

### Prerequisites

- Python 3.11+
- `ffmpeg` — voice transcription (`sudo apt install ffmpeg`)
- Telegram bot token (via @BotFather)
- OpenRouter API key

### Installation

```bash
git clone https://github.com/serbskisyn/Serbo_bot.git
cd Serbo_bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
```

### Trade Engine

```bash
cd /home/pi/trade_engine
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
```

### systemd Services

```bash
# Serbo Bot
sudo cp serbo_bot.service /etc/systemd/system/
sudo systemctl enable --now serbo_bot

# Trade Engine
sudo cp /home/pi/trade_engine/trade_engine.service /etc/systemd/system/
sudo systemctl enable --now trade_engine

# Live logs
sudo journalctl -u serbo_bot -f
sudo journalctl -u trade_engine -f
```

### Environment Variables

**Required:**

| Variable | Description |
| :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs |

**Trading:**

| Variable | Default | Description |
| :--- | :--- | :--- |
| `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` | — | Kraken exchange keys |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | — | Alpaca API keys |
| `ALPACA_PAPER` | `false` | Paper trading mode |
| `ALPACA_STAKE_USD` | `10` | USD per stock trade |
| `KRAKEN_STAKE_AMOUNT` | `0.0003` | BTC per crypto trade |
| `BUY_CONFIDENCE` | `0.65` | Min LLM confidence to buy |
| `SELL_CONFIDENCE` | `0.65` | Min LLM confidence to sell |
| `STOP_LOSS_PCT` | `0.02` | Hard stop-loss (2%) |
| `TRAILING_ACTIVATE_PCT` | `0.02` | Trailing stop activates at +2% |
| `TRAILING_TRAIL_PCT` | `0.01` | Trailing distance (1%) |
| `TRADE_ENGINE_URL` | `http://127.0.0.1:8081` | Trade Engine API |
| `TRADE_ENGINE_SECRET` | — | API auth secret |

**APIs:**

| Variable | Description |
| :--- | :--- |
| `TAVILY_API_KEY` | Web search (free: 1,000/month) |
| `GNEWS_API_KEY` | Football news (optional) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google Sheets + Calendar |

---

## Stack

| Component | Purpose |
| :--- | :--- |
| `python-telegram-bot 22.x` | Telegram interface + JobQueue |
| `langgraph` | Multi-agent state machine |
| `langgraph-checkpoint-sqlite` | Async SQLite conversation checkpoints |
| `fastapi` + `uvicorn` | Trade Engine REST API |
| `ccxt` | Kraken exchange (crypto) |
| `alpaca-py` | Alpaca Markets (US stocks) |
| `aiosqlite` | Async SQLite (positions, trade log) |
| `httpx` | Async HTTP (OpenRouter, Tavily, feeds) |
| `pandas` | OHLCV data + indicator calculation |
| `openai-whisper` | Local voice transcription |
| `matplotlib` | Chart rendering → PNG |
| `gspread` | Google Sheets read/write |
| `tavily-python` | Web search |
| OpenRouter | LLM backbone (GPT-4o-mini default) |

---

## Roadmap

### Done
- [x] LangGraph multi-agent architecture (Supervisor + 4 agents)
- [x] LLM routing — confidence scoring + topic-carry
- [x] Per-user fact memory (confirmed + pending, threshold 5)
- [x] Football News Agent — 4-layer aggregation, LLM enrichment, 48h cache
- [x] Shift scheduler — 8 hard + 3 soft constraints, Google Sheets I/O
- [x] Strava Kudos automation
- [x] Claude Code CLI integration (`/claude`, `/claudex`)
- [x] Two-stage injection guard (pattern + LLM)
- [x] **Trade Engine** — unified Crypto + US-Stock service
- [x] 5m candles + 5min scan interval
- [x] Price Monitor — 30s stop-loss + trailing stop (no LLM)
- [x] Sentiment filter — Fear & Greed, Polymarket, Tavily
- [x] 15 Crypto pairs (Kraken/BTC) + 15 US symbols (Alpaca)

### Planned
- [ ] **Scrapling integration** — `StealthyFetcher` for Strava (replace fragile cookie auth), `AsyncFetcher` for financial news in sentiment block
- [ ] Freqtrade shutdown after Trade Engine proves stable (~2 weeks)
- [ ] `/tradebot` + `/stocks` extended — trade history, daily P&L chart
- [ ] Football News fact-check + quality score
- [ ] Webhook mode (lower latency vs polling)

---

## License

Private project — not licensed for public use.
