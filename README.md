<h1 align="center">Serbo Bot</h1>

<p align="center">
  <strong>Production-grade Telegram AI Assistant + Lead Qualifying Pipeline</strong><br>
  <sub>Running on Raspberry Pi · Powered by LLMs · Multi-Agent · Always On</sub>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python" />
  <img src="https://img.shields.io/badge/Platform-Raspberry%20Pi-red?style=flat-square&logo=raspberrypi" />
  <img src="https://img.shields.io/badge/LLM-OpenRouter-orange?style=flat-square" />
  <img src="https://img.shields.io/badge/License-Private-lightgrey?style=flat-square" />
</p>

---

Serbo Bot is a modular, self-hosted Telegram bot that combines a full **multi-agent AI assistant** with a **B2B lead qualifying pipeline**. It runs 24/7 on a Raspberry Pi as three independent systemd services. Every message passes through a three-layer security stack before reaching any agent; leads are enriched via Perplexity, validated against Pepper Intelligence, and written back to Google Sheets automatically.

---

## Bot Commands

| Command | Description |
| :--- | :--- |
| `/start` | Welcome message + full command reference |
| `/help` | Same as `/start` |
| `/news` | Football news for your favourite clubs (SQLite cache, 48 h TTL) |
| `/news fresh` | Force live re-fetch, bypass cache |
| `/xnews <query>` | X.com live search via Grok — real-time results |
| `/leads` | Run lead qualifying pipeline (up to `LEAD_QUALIFYING_MAX_PER_RUN` leads) |
| `/leads <N>` | Run exactly N leads (overrides env-var limit) |
| `/leads rerun <Zeile>` | Re-process a single sheet row (e.g. `/leads rerun 90`) |
| `/termine [heute\|morgen\|woche]` | Google Calendar events for today / tomorrow / this week |
| `/kalender1` | Switch to Gmail calendar |
| `/kalender2` | Switch to Workspace calendar |
| `/stocks` | Alpaca account status + open positions |
| `/stocks scan` | Trigger manual LLM scan across US stock watchlist |
| `/tradebot` | Kraken crypto trade engine status (positions, P&L, stats) |
| `/tradebot crypto pause` | Stop new crypto buys |
| `/tradebot crypto resume` | Re-enable crypto buys |
| `/claude <query>` | Claude Code CLI — text only, no tool access |
| `/claudex <task>` | Claude Agent session — full tool access (files, git, bash) |
| `/health` | System health check (services, APIs, disk) |
| `/tests` | Run full test suite (Serbo Bot + Trade Engine) |
| `/dienstplan` | Interactive 3-shift nursing schedule builder |
| `/strava` | Give Kudos to all Strava feed activities |
| `/reset` | Clear conversation history |
| `/memory` | Show confirmed + pending facts the bot knows about you |
| `/forget` | Wipe all memory |

---

## Architecture

```
Telegram User
  |
  v
Whitelist --> Rate Limiter --> Injection Guard (pattern + LLM)
  |
  v
handlers.py (command routing)
  |
  v
+------------------------------------------+
|  LangGraph Supervisor (agents/runner.py)  |
|   routes to: General | Football | Chart   |
|              Web | Lead Qualifying        |
+------------------------------------------+
  |
  v
Memory extraction --> reply to Telegram
```

**State machine** (`agents/graph.py`, `agents/state.py`):
- `BotState` TypedDict carries `user_id`, `text`, `agent`, `response`, `messages`, `chart_bytes`, `topic`, `confidence`
- Supervisor routes via LLM JSON classification; confidence + topic carry forward for ambiguous follow-ups (`CONFIDENCE_THRESHOLD = 0.60`)
- Conversation history checkpointed in `app/data/conversation.db` via `AsyncSqliteSaver`, keyed by `user_id`
- Chart responses signalled by `"__CHART__"` sentinel; runner converts PNG bytes to a Telegram photo

---

## Lead Qualifying Pipeline

```
Inbound Google Sheet (new rows)
  |
  v
pre_qualify  (fast LLM filter — HIGH / LOW / AGENCY / SKIP)
  |-- SKIP / AGENCY --> collect_filtered_result (written to sheet, no Telegram)
  |
  v (HIGH / LOW)
discover_brands  (Perplexity: find eCommerce brands under the company)
  |
  v
validate_company  (Perplexity: validate brands + revenue / employees / HQ / model)
  |
  v
enrich_commercial_intelligence  (Perplexity: marketing spend, affiliate networks,
                                  perf-mktg signals, promo intensity)
  |
  v
enrich_contact_v2  (Perplexity: title, seniority, authority, role-match, LinkedIn)
  |
  v
[Hard-Skip Check]
  |-- B2B / no eCommerce signal --> skip_pepper --> qualify_business_fit
  |
  v (eCommerce relevant)
pepper_multi_country  (Pepper Intelligence MCP — RAG-compact sentiment per brand,
                        domestic target country + cross-country)
  |
  v
qualify_business_fit  (deterministic score 0-100 + LLM priority tier + action)
  |
  v
collect_result
  |
  v
write_results  --> Google Sheet (8 Validation columns) + Telegram summary
```

**Pre-qualify labels:**
- `HIGH` — clear eCommerce/Retail/Travel/Finance signal → full enrichment
- `LOW` — unclear, enriched anyway for safety
- `AGENCY` — media/ad agency (Publicis, Dentsu, WPP etc.) → written as AGENCY, skips enrichment
- `SKIP` → written as FILTERED, no Telegram notification

**Hard-skip conditions** (Pepper lookup bypassed when ANY is true):
- `business_model == "B2B"` — pure B2B, no consumer deal-platform presence
- `validated_brands` is empty AND `contact_role_match` is `False` AND `contact_authority == "other"`

**Scoring (scorer_v2.py) — deterministic, 0-100:**

| Component | Max | Key inputs |
| :--- | ---: | :--- |
| Business model | 12 | B2C/D2C/Marketplace/B2B |
| Company size | 10 | Employee count + revenue |
| Brand count | 8 | Validated eCommerce brands |
| Pepper target volume | 20 | Mentions in target country (noise floor: < 5 = 0 pts) |
| Pepper sentiment | 10 | Positive rate in target country |
| Pepper cross-country | 10 | Markets with ≥ 50 mentions |
| Contact seniority | 8 | Senior / Mid / Junior |
| Contact authority | 4 | Decision-maker / influencer |
| LinkedIn found | 2 | URL present |
| Role match | 1 | Marketing / eCommerce role |
| Market overlap | 8 | Primary markets ∩ Atolls markets |
| Sales signals | 7 | Keyword density in signals text |

**Override rules (applied after raw score):**
- `total_pepper == 0` → cap at COLD (≤ 39)
- `total_pepper ≤ 20 AND 0 positive mentions` → cap at COLD (micro-signal noise)
- `target > 2000 mentions AND pos_rate ≥ 55%` → force HOT (≥ 70)
- `B2B/Manufacturer AND 0 brands AND 0 pepper` → force COLD

**Classification:** HOT ≥ 70 · WARM 40–69 · COLD < 40

**Google Sheet — Validation columns (8):**

| Column | Content |
| :--- | :--- |
| `Validation_Brands` | Validated eCommerce brand names |
| `Validation_Pepper` | Target + cross-country Pepper sentiment (RAG-compact) |
| `Validation_Context` | Company facts + contact details + commercial intel |
| `Validation_Score` | Score 0-100 |
| `Validation_Classification` | HOT / WARM / COLD / FILTERED / AGENCY |
| `Validation_Priority_Tier` | LOW / MEDIUM / HIGH / STRATEGIC |
| `Validation_Note` | Recommended action + score breakdown + sales signals |
| `Validation_Date` | ISO date — also used as idempotency marker |

**Triggering manually:**
```
/leads          -- up to LEAD_QUALIFYING_MAX_PER_RUN (env default 30)
/leads 5        -- exactly 5 leads
/leads rerun 90 -- re-process sheet row 90
```

---

## Agent Overview

| Agent | File | Purpose |
| :--- | :--- | :--- |
| Supervisor | `agents/nodes/supervisor.py` | LLM routing with confidence scoring + topic-carry |
| General | `agents/general_agent.py` | General Q&A with per-user memory |
| Football | `agents/football_news_agent.py` | Live football news + club data via Tavily |
| Chart | `agents/chart_agent.py` | LLM generates matplotlib code → PNG |
| Web | `agents/web_agent.py` | Tavily web search → German summary |
| Lead Qualifying | `agents/lead_qualifying/` | B2B lead enrichment pipeline (see above) |
| XNews | `agents/xnews_agent.py` | X.com live search via Grok (OpenRouter) |

---

## Security Layers

| Layer | File | What it does |
| :--- | :--- | :--- |
| Whitelist | `bot/whitelist.py` | Rejects all Telegram user IDs not in `ALLOWED_USER_IDS` |
| Rate Limiter | `security/rate_limiter.py` | Sliding window — max N messages per window per user |
| Injection Guard | `security/injection_guard.py` | Two-stage: (1) pattern + homoglyph regex, (2) LLM-Guard via claude-haiku |

Always call `is_injection_async()` (async two-stage); never the sync wrapper inside async handlers.

---

## Environment Variables

### Core

| Variable | Default | Description |
| :--- | :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | required | Bot token from @BotFather |
| `OPENROUTER_API_KEY` | required | OpenRouter API key |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | LLM model identifier |
| `ALLOWED_USER_IDS` | `""` | Comma-separated Telegram user IDs whitelist |
| `BOT_NAME` | `MeinAgent` | Bot display name |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### Security / Rate Limiting

| Variable | Default | Description |
| :--- | :--- | :--- |
| `RATE_LIMIT_MAX_REQUESTS` | `10` | Max messages per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit window (seconds) |

### APIs

| Variable | Default | Description |
| :--- | :--- | :--- |
| `TAVILY_API_KEY` | required | Tavily web search key |
| `GNEWS_API_KEY` | `""` | GNews API key (optional, football news) |
| `GROK_API_KEY` | `""` | Grok API key for `/xnews` (X.com live search) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | `""` | Google Sheets + Calendar service account credentials |
| `GCAL_CALENDAR_ID_1` | `""` | Gmail calendar ID |
| `GCAL_CALENDAR_ID_2` | `""` | Workspace calendar ID |

### Lead Qualifying

| Variable | Default | Description |
| :--- | :--- | :--- |
| `LEAD_QUALIFYING_MAX_PER_RUN` | `30` | Max leads processed per pipeline run |
| `LEAD_QUALIFYING_TIMES` | `08:00,16:00` | Scheduled run times (comma-separated HH:MM, Europe/Berlin) |
| `LEAD_QUALIFYING_SCHEDULER_ENABLED` | `true` | Set to `false` to disable scheduled runs |
| `SCHEDULE_OUTPUT_SHEET_ID` | (hardcoded) | Google Sheet ID for schedule output |

### News Pipeline

| Variable | Default | Description |
| :--- | :--- | :--- |
| `NEWS_CACHE_MAX_AGE_HOURS` | `48` | News cache TTL |
| `NEWS_SCHEDULER_BASE_MINUTES` | `45` | Background cache refresh interval |
| `NEWS_DAILY_PUSH_HOUR` | `6` | Daily push time (hour, CEST) |
| `NEWS_DAILY_PUSH_MINUTE` | `30` | Daily push time (minute) |
| `NEWS_DAILY_PUSH_USER_IDS` | `""` | Users receiving daily news push |

### Trading

| Variable | Default | Description |
| :--- | :--- | :--- |
| `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` | — | Kraken exchange keys |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | — | Alpaca API keys |
| `ALPACA_PAPER` | `false` | Paper trading mode |
| `TRADE_ENGINE_URL` | `http://127.0.0.1:8081` | Trade Engine REST API |
| `TRADE_ENGINE_SECRET` | — | API auth secret |

---

## Running

```bash
# Start bot
python -m app.main

# Run tests
pytest tests/ -v

# Restart systemd service
sudo systemctl restart serbo_bot

# Re-run a single lead by sheet row number
python scripts/rerun_lead_row.py <row>

# Live logs
sudo journalctl -u serbo_bot -f
```

---

## Infrastructure

Three independent systemd services running on Raspberry Pi:

| Service | Path | Description |
| :--- | :--- | :--- |
| `serbo_bot` | `/home/pi/Serbo_bot` | Telegram bot + lead qualifying + news pipeline |
| `trade_engine` | `/home/pi/trade_engine` | Unified Crypto (Kraken) + US-stock (Alpaca) trading service, REST API on port 8081 |
| `freqtrade` | `/home/pi/freqtrade` | Legacy LLM crypto strategy (running in parallel during Trade Engine validation) |

```bash
sudo systemctl status serbo_bot trade_engine freqtrade
sudo journalctl -u serbo_bot -f
sudo journalctl -u trade_engine -f
```

---

## License

Private project — not licensed for public use.
