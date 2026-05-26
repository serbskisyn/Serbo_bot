<h1 align="center">Serbo Bot</h1>

<p align="center">
  <strong>Production-grade Telegram AI Assistant + Personal-Productivity Layer + Lead Qualifying Pipeline</strong><br>
  <sub>Running on Raspberry Pi · Powered by LLMs · Multi-Agent · Always On</sub>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python" />
  <img src="https://img.shields.io/badge/Platform-Raspberry%20Pi-red?style=flat-square&logo=raspberrypi" />
  <img src="https://img.shields.io/badge/LLM-OpenRouter-orange?style=flat-square" />
  <img src="https://img.shields.io/badge/License-Private-lightgrey?style=flat-square" />
</p>

---

Serbo Bot is a modular, self-hosted Telegram bot that combines a full **multi-agent AI assistant**, a **personal productivity layer** (structured profile, todo engine, daily briefing/reflection, Granola meeting ingest), and a **B2B lead qualifying pipeline**. It runs 24/7 on a Raspberry Pi as three independent systemd services.

Every message passes through a three-layer security stack before reaching any agent. Leads are enriched via Perplexity and validated against Pepper Intelligence. Meeting commitments are pulled from Granola and surfaced in a morning briefing alongside calendar events. A semantic layer (sqlite-vec + OpenAI embeddings) deduplicates paraphrased todos and synonym people-names across all ingest sources.

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
| `/memory` | Show structured profile (identity, work, interests, people, projects, goals, facts) |
| `/forget` | Wipe all memory |
| `/todo` | List today's open todos sorted by priority |
| `/todo add <text> [datum]` | Add a todo — date hints: `heute`, `morgen`, `freitag`, `30.05`, `2026-05-30` |
| `/todo list [today\|week\|all]` | List todos in scope |
| `/todo done <id>` | Mark a todo done (also auto-detected from chat) |
| `/todo snooze <id> <days>` | Snooze a todo N days |
| `/todo drop <id>` | Drop without doing |
| `/todo stats` | Counts per status |
| `/briefing` | Show today's morning briefing manually (auto-pushed 07:30) |
| `/reflect` | Show today's evening reflection manually (auto-pushed 21:30) |

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
reply to Telegram
  |
  └─── fire-and-forget background extractors:
         profile_learner    → profile.yaml
         todo_extractor     → todos.db (new todos with source="chat")
         completion_extract → todos.db (mark_done via semantic match)

Scheduled jobs (Europe/Berlin):
  06:15 granola_daily_pull       (Granola MCP → todos + people)
  07:30 daily_briefing           (🌅 morning push)
  +6h   gcal_ingest_periodic     (calendar → prep-todos)
  21:30 evening_reflection       (🌙 evening push)
  23:00 daily_session_summaries  (conversation recap)
```

**State machine** (`agents/graph.py`, `agents/state.py`):
- `BotState` TypedDict carries `user_id`, `text`, `agent`, `response`, `messages`, `chart_bytes`, `topic`, `confidence`
- Supervisor routes via LLM JSON classification; confidence + topic carry forward for ambiguous follow-ups (`CONFIDENCE_THRESHOLD = 0.60`)
- Conversation history checkpointed in `app/data/conversation.db` via `AsyncSqliteSaver`, keyed by `user_id`
- Chart responses signalled by `"__CHART__"` sentinel; runner converts PNG bytes to a Telegram photo

---

## Personal Productivity (Jarvis Mode)

A 5-layer system that builds a structured user profile, tracks todos with priority, ingests commitments from chat / Granola / Google Calendar, and bookends each day with a 07:30 briefing + 21:30 reflection.

```
              ┌────────────────────────────────────────────┐
              │ Chat (every message, fire-and-forget)       │
              │  profile_learner (3-stage: detect→write→review)
              │  todo_extractor (commitments → todos)        │
              │  completion_extractor (done-detections → mark_done)
              └─────────────┬──────────────────────────────┘
                            │
   ┌────────────────────────┼────────────────────┬───────────────────┐
   │                        │                    │                   │
   ▼                        ▼                    ▼                   ▼
profile.yaml          todos.db                semantic.db        summaries/
identity / work     id, text, source, due,    sqlite-vec +       per-day MD
interests, people   priority, mentions,       OpenAI embeddings  (reflection)
projects, goals     status, snoozed_until     dedup across all
facts, pending                                collections
   ▲                        ▲                    ▲
   │                        │                    │
   │                  Granola MCP           backfill script
   │              (06:15 daily pull,        (one-shot at upgrade)
   │               commitments + decisions
   │               + mentioned_people)
   │
   │              Google Calendar
   │              (every 6h, keyword-match titles → prep-todos)
   │
   └──────────── Profile Learner (sets identity / work / interests)
```

### Daily rhythm

| Time (Europe/Berlin) | Job | What it does |
| :--- | :--- | :--- |
| **06:15** | `granola_daily_pull` | Pulls last 30h Granola meetings → commitments + decisions + people |
| **06:30** | `gcal_daily_summary` | Pre-existing — pushes today's calendar events |
| **07:00** | `daily_health_check` | Pre-existing — services / APIs / disk |
| **07:30** | `daily_briefing` | 🌅 Morning push: today's events + top todos + yesterday's decisions + relationship alerts |
| **+6h** | `gcal_ingest_periodic` | Every 6 hours — scans next 7d events, creates prep-todos for keyword-matched titles |
| **21:30** | `evening_reflection` | 🌙 Day-end push: what got done today + what's still open + decisions logged |
| **23:00** | `daily_session_summaries` | Pre-existing — markdown summary of new info from today's conversation |

### Layer 1 — Structured profile (`app/bot/profile.py`)

YAML-backed hierarchical user model at `app/data/profile.yaml`:

```yaml
identity:    {name, location, age, birthplace, role}
work:        {company, team, industry, position}
interests:   [list of strings]
preferences: {communication_style, ...}
people:      [{name, relation, last_mentioned, notes}, ...]
projects:    [{name, status, notes}, ...]
goals:       [{text, timeframe}, ...]
facts:       {free-form key/value bucket}
pending:     [{text, type, confidence, mentions}, ...]
```

`app/bot/memory.py` is kept as a backwards-compat shim so older agents (football_news, session_summary, general node) continue to work unchanged.

### Layer 2 — 3-stage learner (`app/services/profile_learner.py`)

Runs fire-and-forget after every chat reply. Three sequential LLM calls (gpt-4o-mini, ~$0.0001 per message total):

1. **Detector** — *"Is there anything new about the USER worth remembering?"* Filters out weather/dates/Meta-statements/assistant-suggestions.
2. **Writer** — Maps clean candidates to structured operations (`{section, op, key, value, confidence}`).
3. **Reviewer** — Validates ops against the current profile, rejects duplicates / contradictions / low-confidence.

Each stage degrades gracefully — a failure in any one returns an empty result instead of raising.

### Layer 3 — ToDo engine (`app/services/todos.py`)

Async SQLite store at `app/data/todos.db`. Priority is computed at read time:

```
base    = 1.0 overdue/today | 0.7 within 3d | 0.5 else
factor  = 0.5 + min(mention_count, 10) / 10
decay   = 1.0 today | 0.85 within 7d | 0.7 older
score   = base * factor * decay
```

The `mention_existing` function deduplicates by:
1. exact case-insensitive text match (fast path)
2. **semantic** match via sqlite-vec (catches paraphrases — "Send tracking links" ≈ "Share tracking URLs")

A duplicate add bumps `mention_count` instead of inserting — repeated mentions automatically rise to the top.

### Layer 4 — Ingest sources (`app/services/{granola_lookup,granola_sync,gcal_ingest,todo_extractor,completion_extractor}.py`)

| Source | Trigger | Mechanism | Output |
| :--- | :--- | :--- | :--- |
| **Chat** | Every message | LLM extractor (gpt-4o-mini) | New todos with `source="chat"` |
| **Granola** | Daily 06:15 | Claude subprocess + Granola MCP, 2-attempt retry | Commitments → todos; Decisions → "Entscheidung:…" todos; Mentioned people → `profile.people` |
| **Google Calendar** | Every 6h | Keyword match (no LLM) — `vorbereiten`, `demo`, `pitch`, `review`, etc. while ignoring `standup`, `lunch`, `focus`, `urlaub` | "Vorbereiten: <event>" todos due 1 day before event |
| **Manual** | `/todo add` | Direct DB insert | `source="manual"` |

All MCP-via-Claude calls (Pepper + Granola) route through `app/services/mcp_runner.py`, which holds a global `asyncio.Semaphore(1)` — prevents OOM on Pi when /leads and /briefing overlap.

### Layer 5 — Semantic layer (`app/services/semantic.py` + `embeddings.py`)

sqlite-vec virtual tables (`vec_todos`, `vec_people`, `vec_decisions`) backed by OpenAI text-embedding-3-small. L2 distance on unit-normalised vectors (≈ cosine similarity):

| Use case | Threshold | Calibrated against |
| :--- | ---: | :--- |
| Todo paraphrase dedup | `< 0.85` | "Send tracking-link URLs" vs "Send tracking links for testing" = 0.71 |
| People name dedup | `< 0.95` | "Ollie" vs "Oliver" = 0.74 |
| Fuzzy related-search | `< 1.10` | — |
| Loose recall | `< 1.30` | "Brot kaufen" vs unrelated todos = 1.25 |

A binary append-only embedding cache at `app/data/embedding_cache.bin` (32B SHA-256 + 6 KB float32 per vector) prevents re-embedding the same exact string.

### Auto-completion detection

The `completion_extractor` runs alongside the profile + todo extractors on every chat. When the user says "habe X erledigt" / "X ist fertig" / "done with Y", the extractor:

1. LLM detects the completed action
2. semantic.find_similar against open todos (threshold = FUZZY 1.10)
3. todos.mark_done on the closest hit
4. semantic.delete cleans up the embedding row

The user sees the effect in the next /todo list or the next morning briefing.

### Phase 1 → Phase 6 file map

| Phase | New files | Purpose |
| :--- | :--- | :--- |
| 1 — Profile | `bot/profile.py`, `services/profile_learner.py` | Structured YAML + 3-stage learner |
| 2 — ToDos | `services/todos.py`, `bot/todo_commands.py` | Async SQLite + `/todo` command |
| 3 — Ingest | `services/{granola_lookup,granola_sync,gcal_ingest,todo_extractor,mcp_runner}.py`, `bot/sync_jobs.py` | 3 auto-sources + MCP serialisation |
| 4 — Briefing | `services/briefing.py`, `bot/briefing_job.py` | 🌅 07:30 push |
| 5 — Semantic | `services/{semantic,embeddings}.py` | sqlite-vec dedup |
| 6 — Reflection | `services/{evening_reflection,completion_extractor}.py`, `bot/evening_job.py` | 🌙 21:30 push + auto-done |

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

### Personal Productivity (Jarvis Mode)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `BRIEFING_HOUR` | `7` | Morning briefing push hour (Europe/Berlin) |
| `BRIEFING_MINUTE` | `30` | Morning briefing push minute |
| `BRIEFING_ENABLED` | `true` | Set to `false` to disable the scheduled push |
| `BRIEFING_TOP_TODOS` | `6` | How many top-priority todos to render in the briefing |
| `BRIEFING_RELATIONSHIP_ALERT_DAYS` | `21` | Days a person can stay unmentioned before showing up under 🤝 |
| `REFLECTION_HOUR` | `21` | Evening reflection push hour |
| `REFLECTION_MINUTE` | `30` | Evening reflection push minute |
| `REFLECTION_ENABLED` | `true` | Set to `false` to disable the evening push |

The morning + evening pushes go to every user in `NEWS_DAILY_PUSH_USER_IDS`.

### News Pipeline

| Variable | Default | Description |
| :--- | :--- | :--- |
| `NEWS_CACHE_MAX_AGE_HOURS` | `48` | News cache TTL |
| `NEWS_SCHEDULER_BASE_MINUTES` | `45` | Background cache refresh interval |
| `NEWS_DAILY_PUSH_HOUR` | `6` | Daily push time (hour, CEST) |
| `NEWS_DAILY_PUSH_MINUTE` | `30` | Daily push time (minute) |
| `NEWS_DAILY_PUSH_USER_IDS` | `""` | Users receiving daily news + briefing + reflection pushes |

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

# One-shot upgrades
python -m scripts.migrate_memory_to_profile   # legacy memory.json → profile.yaml
python -m scripts.backfill_embeddings         # embed existing todos + people

# Live logs
sudo journalctl -u serbo_bot -f
```

### Data files

| File | Format | Purpose |
| :--- | :--- | :--- |
| `app/data/conversation.db` | SQLite (LangGraph) | Per-user conversation history |
| `app/data/profile.yaml` | YAML | Structured user profile (identity/work/interests/people/projects/goals/facts) |
| `app/data/todos.db` | SQLite | Per-user todo store |
| `app/data/semantic.db` | SQLite (sqlite-vec) | Vector store for cross-cutting dedup |
| `app/data/embedding_cache.bin` | Binary | Append-only OpenAI embedding cache |
| `app/data/news_cache.db` | SQLite | Cached football news articles |
| `app/data/summaries/` | Markdown | Daily session + reflection summaries |
| `app/data/briefing_state.json` | JSON | Idempotency marker for morning push |
| `app/data/reflection_state.json` | JSON | Idempotency marker for evening push |
| `app/data/memory.json.legacy` | JSON | Archived legacy flat memory (post-migration) |

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
