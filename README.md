# Serbo Bot — Telegram AI Assistant

A modular, production-grade Telegram bot running on a Raspberry Pi. Powered by LLMs via OpenRouter with a full multi-agent LangGraph architecture, 4-layer news aggregation, constraint-based shift scheduling, Strava automation, and direct Claude Code CLI access via Telegram commands.

---

## Feature Overview

| Status | Feature |
| :--- | :--- |
| ✅ | Telegram bot interface (text, voice, commands) |
| ✅ | LangGraph multi-agent state machine (Supervisor → 4 agents) |
| ✅ | LLM-based supervisor routing with confidence scoring + topic-carry |
| ✅ | Football safety net: club name + football term → always routes to football |
| ✅ | Persistent conversation memory (SQLite via AsyncSqliteSaver) |
| ✅ | Voice messages — transcribed via local Whisper model (lazy-loaded) |
| ✅ | General Agent — concise LLM assistant with user memory context |
| ✅ | Football Agent — live stats via Tavily when current data is requested |
| ✅ | Chart Agent — LLM-generated matplotlib code → PNG sent via Telegram |
| ✅ | Web Agent — live web search via Tavily, synthesised into German summary |
| ✅ | Football News Agent — `/news` command, 4-layer aggregation pipeline |
| ✅ | News Cache — SQLite-backed, 48h TTL, background refresh every 45 ± 15 min |
| ✅ | News Ranking — Jaccard-clustering (threshold 0.25) + source count scoring |
| ✅ | News Enrichment — LLM generates German headline + 50-word snippet per article |
| ✅ | Re-clustering after LLM enrichment + semantic top-5 deduplication |
| ✅ | Daily news push — scheduled briefing at 06:30 via JobQueue |
| ✅ | Feed health tracking — alerts admin if >50% of feeds fail |
| ✅ | Club configuration — `config/clubs.json` with aliases, feeds, exclude keywords |
| ✅ | Retry/Backoff — exponential backoff (2^n s) on 429/502/503 for all feeds |
| ✅ | Strava Kudos — `/strava` command, session-cookie auth, auto-likes feed |
| ✅ | Shift scheduler — 3-shift nursing schedule with 8 hard + 3 soft constraints |
| ✅ | Schedule orchestrator — 5 sub-agents + KontrollAgent + multi-pass refinement |
| ✅ | Google Sheets integration — staff, vacation, sick leave, wishes, output |
| ✅ | `/claude` — Claude Code CLI in text-only mode |
| ✅ | `/claudex` — Claude Agent with full tool access (files, Git, Bash) |
| ✅ | Two-stage prompt injection guard (pattern + homoglyph + LLM-Guard, async) |
| ✅ | Rate limiting — sliding window per user (configurable) |
| ✅ | User whitelist — only allowed Telegram IDs |
| ✅ | Per-user fact memory — confirmed (direct) + pending (indirect, threshold 5) |
| ✅ | Automatic fact extraction after every message via LLM |
| ✅ | OpenRouter integration — any LLM (GPT-4o, Claude, Mistral, ...) |
| ✅ | GitHub Actions CI — pytest on every push |

---

## Architecture

```
User (Telegram — text / voice / command)
  │
  ├─ Whitelist (ALLOWED_USER_IDS)
  ├─ Rate Limiter (sliding window per user)
  ├─ Injection Guard (2-stage: pattern + LLM)
  │
  ├─ /news ──────────────────────────────────► Football News Agent
  ├─ /strava ─────────────────────────────────► Strava Kudos Bot
  ├─ /dienstplan ──────────────────────────────► Schedule Dialog → Orchestrator
  ├─ /claude ──────────────────────────────────► Claude CLI (--print)
  ├─ /claudex ─────────────────────────────────► Claude Agent (--dangerouslySkipPermissions)
  │
  └─ text / voice ─────────────────────────────► LangGraph Runner
                                                   │
                                              Supervisor Node
                                            (LLM routing, confidence)
                                                   │
                          ┌────────────┬───────────┼───────────┐
                          │            │           │           │
                      General      Football     Chart        Web
                       Node         Node        Node        Node
                    (LLM + mem)  (Tavily opt) (matplotlib) (Tavily)
                          │            │           │           │
                          └────────────┴───────────┴───────────┘
                                                   │
                                        Fact Extraction (async)
                                        Memory Update (confirmed/pending)
                                                   │
                                             Reply to User
```

---

## Telegram Commands

| Command | Description |
| :--- | :--- |
| `/start` | Welcome message with full command list |
| `/reset` | Clear conversation history |
| `/memory` | Show confirmed + pending facts about you |
| `/forget` | Wipe all memory |
| `/news` | Latest news for your favourite clubs (from memory) |
| `/news fresh` | Force live re-fetch (bypasses cache) |
| `/strava` | Give Kudos to all activities in your Strava feed |
| `/claude <prompt>` | Ask Claude Code CLI (text-only, no tool use) |
| `/claudex <task>` | Claude Agent with full tool access — can read/write files, run git, commit |
| `/dienstplan` | Interactive 3-step shift schedule builder |
| `/debugwunsch` | Diagnose Google Sheets structure (admin) |

---

## Directory Structure

```
Serbo_bot/
├── app/
│   ├── main.py                          Entry point, handler registration
│   ├── config.py                        Environment config + validation
│   │
│   ├── agents/
│   │   ├── state.py                     BotState TypedDict
│   │   ├── graph.py                     LangGraph StateGraph
│   │   ├── runner.py                    AsyncSqliteSaver + ainvoke
│   │   ├── football_news_agent.py       /news orchestrator
│   │   ├── chart_agent.py               Chart code generation + PNG export
│   │   │
│   │   ├── nodes/
│   │   │   ├── supervisor.py            LLM routing (confidence + topic-carry)
│   │   │   ├── general.py               General LLM node (with memory context)
│   │   │   ├── football.py              Football node (+ Tavily live data)
│   │   │   ├── chart.py                 Chart generation node
│   │   │   └── web.py                   Web search node
│   │   │
│   │   └── schedule/
│   │       ├── orchestrator.py          Coordination + control loop (max 3 rounds)
│   │       ├── mitarbeiter_agent.py     Load staff from Google Sheets
│   │       ├── urlaub_agent.py          Load vacation
│   │       ├── krank_agent.py           Load sick leave
│   │       ├── vormonat_agent.py        Load previous month (block continuity)
│   │       ├── wunsch_agent.py          Load shift wishes
│   │       └── kontroll_agent.py        Validate plan, report violations
│   │
│   ├── bot/
│   │   ├── handlers.py                  All Telegram handlers
│   │   ├── whitelist.py                 User auth check
│   │   ├── conversation.py              In-memory chat history (deque, max 20)
│   │   ├── memory.py                    Per-user fact store (JSON-persisted)
│   │   ├── daily_news_job.py            Scheduled daily news push (JobQueue)
│   │   ├── schedule_dialog.py           /dienstplan ConversationHandler (3 steps)
│   │   ├── debug_handler.py             /debugwunsch Sheet diagnostics
│   │   └── bot_context.py               Global bot instance (for background alerts)
│   │
│   ├── security/
│   │   ├── injection_guard.py           2-stage injection detection
│   │   └── rate_limiter.py              Sliding window per user
│   │
│   ├── services/
│   │   ├── openrouter_client.py         LLM API client + fact extractor
│   │   ├── news_fetcher.py              4-layer aggregation + FeedHealthTracker
│   │   ├── news_cache.py                SQLite cache + background scheduler
│   │   ├── news_ranker.py               Jaccard clustering + source scoring
│   │   ├── news_enricher.py             LLM headline + snippet generation
│   │   ├── schedule_builder.py          DienstplanGenerator + constraint engine
│   │   ├── gspread_client.py            Google Sheets I/O
│   │   ├── claude_runner.py             Claude CLI subprocess wrapper
│   │   ├── web_search.py                Tavily client
│   │   └── speech_to_text.py            Whisper transcription (lazy model load)
│   │
│   └── data/
│       ├── memory.json                  Per-user facts (auto-created)
│       ├── conversation.db              LangGraph SQLite checkpoint (auto-created)
│       └── news_cache.db                News cache (auto-created)
│
├── config/
│   └── clubs.json                       Club aliases, RSS feeds, exclude keywords
│
├── strava_kudos/
│   ├── kudos_bot.py                     Strava session-cookie automation
│   ├── session.json                     Strava session (auto-created, git-ignored)
│   ├── .env.example
│   └── requirements.txt
│
├── tests/
│   ├── conftest.py
│   └── test_injection_guard.py
│
├── .env.example
├── requirements.txt
├── pytest.ini
└── CLAUDE.md
```

---

## Setup

### Prerequisites

- Python 3.11+
- `ffmpeg` — required for voice transcription (`pydub`)
- Telegram bot token (via @BotFather)
- OpenRouter API key
- Tavily API key (free tier: 1,000 searches/month)
- GNews API key (optional, free tier: 100 req/day)

### Installation

```bash
git clone https://github.com/serbskisyn/Serbo_bot.git
cd Serbo_bot
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Linux/Pi
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

### Configuration

```bash
cp .env.example .env
```

#### Required

| Variable | Description |
| :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `OPENROUTER_API_KEY` | API key from openrouter.ai |

#### LLM

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | Model ID for routing + agents |
| `BOT_NAME` | `MeinAgent` | Bot display name |

#### API Keys

| Variable | Default | Description |
| :--- | :--- | :--- |
| `TAVILY_API_KEY` | required | Web search (free: 1,000/month) |
| `GNEWS_API_KEY` | `""` | GNews (optional, free: 100 req/day) |

#### Access Control

| Variable | Default | Description |
| :--- | :--- | :--- |
| `ALLOWED_USER_IDS` | required | Comma-separated Telegram user IDs |
| `RATE_LIMIT_MAX_REQUESTS` | `10` | Max messages per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Window duration in seconds |

#### News

| Variable | Default | Description |
| :--- | :--- | :--- |
| `NEWS_FAVORITE_CLUBS` | `Borussia Dortmund,Dynamo Dresden` | Clubs for background auto-fetch |
| `NEWS_CACHE_MAX_AGE_HOURS` | `48` | Article TTL in cache |
| `NEWS_SCHEDULER_BASE_MINUTES` | `45` | Background refresh interval |
| `NEWS_SCHEDULER_JITTER_MINUTES` | `15` | ±jitter on refresh interval |
| `NEWS_STALE_LABEL_HOURS` | `4` | Show stale-cache label after N hours |

#### Daily News Push

| Variable | Default | Description |
| :--- | :--- | :--- |
| `NEWS_DAILY_PUSH_HOUR` | `6` | Hour (0–23, CEST) |
| `NEWS_DAILY_PUSH_MINUTE` | `30` | Minute (0–59) |
| `NEWS_DAILY_PUSH_USER_IDS` | `ALLOWED_USER_IDS` | Recipients (comma-separated) |

#### Admin Alerts

| Variable | Default | Description |
| :--- | :--- | :--- |
| `ADMIN_CHAT_ID` | first allowed user | Telegram chat ID for feed-health alerts |

#### Schedule / Google Sheets

| Variable | Description |
| :--- | :--- |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Path to Google Cloud service account JSON |
| `SCHEDULE_URLAUB_SHEET_ID` | Spreadsheet ID — vacation |
| `SCHEDULE_WUNSCH_SHEET_ID` | Spreadsheet ID — shift wishes |
| `SCHEDULE_KRANK_SHEET_ID` | Spreadsheet ID — sick leave |
| `SCHEDULE_OUTPUT_SHEET_ID` | Spreadsheet ID — generated schedule output |

#### Misc

| Variable | Default | Description |
| :--- | :--- | :--- |
| `LOG_LEVEL` | `INFO` | Python logging level |

### Run

```bash
# Start bot
python -m app.main

# Run tests
pytest tests/ -v
```

### systemd Service (Raspberry Pi)

```ini
# /etc/systemd/system/serbo_bot.service
[Unit]
Description=Serbo Telegram Bot
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/Serbo_bot
ExecStart=/home/pi/Serbo_bot/.venv/bin/python -m app.main
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable serbo_bot
sudo systemctl start serbo_bot
journalctl -u serbo_bot -f        # live logs
```

---

## Agents

### Supervisor — LLM Routing

Every message passes through the supervisor node first. It calls the LLM with a compact routing prompt and returns `{"agent": "<route>", "confidence": <0.0–1.0>}`.

**Routes**: `general` · `football` · `chart` · `web`

**Confidence threshold**: `0.60`
- Below threshold → carry forward the previous stable topic
- Short follow-ups ("ja", "mehr", "und?") → low confidence (0.2–0.4), topic-carry activates

**Football safety net**: If the text contains any club name *and* a football term, routing is overridden to `football` regardless of LLM output.

### General Node

Concise, direct responses. Prefers bullet points. No filler sentences. Has access to confirmed user facts via the memory prompt (e.g. favourite club, name, location).

### Football Node

Handles all football questions. Detects live-data keywords (table, standings, results, injuries, transfers, fixture, …) and triggers a Tavily web search if found, passing the live context to the LLM.

### Chart Node

LLM generates executable Python/matplotlib code. Code is executed in a temp directory. The resulting PNG is sent directly as a Telegram photo.

### Web Node

Tavily search → LLM synthesises results into a concise German response with source links.

---

## News Pipeline (`/news`)

```
User memory → favourite clubs
       │
       ▼
[Layer 1]  GNews API (max 10 articles, full snippets, retry/backoff)
[Layer 2]  Google News RSS × 2 queries per club ("[Club]" + "[Club] Bundesliga")
[Layer 3]  Static RSS feeds (9 sources):
           sportbild.de · bild.de · skysports.com · sportschau.de · kicker.de
           spox.com · spiegel.de · sueddeutsche.de · transfermarkt.de
[Layer 4]  Club-specific feeds from config/clubs.json
           (MDR + DNN for Dynamo Dresden, extensible per club)
       │
       ▼
Keyword filter (club aliases from clubs.json)
URL + title deduplication
       │
       ▼
Jaccard clustering (threshold 0.25) → group duplicates
Source scoring (cluster size = priority)
       │
       ▼
LLM enrichment: German headline + 50-word snippet per article
Re-clustering after enrichment
LLM semantic deduplication → top 5 per club
       │
       ▼
SQLite cache (48h TTL) + background scheduler (45 ± 15 min)
       │
       ▼
Telegram reply (Markdown, split at 4000 chars)
```

**Force live fetch**: `/news fresh` — bypasses cache, fetches all layers in real time.

### Adding Club Feeds

Edit `config/clubs.json`:

```json
"fc schalke 04": {
  "aliases": ["schalke", "s04"],
  "feeds": [
    "https://www.reviersport.de/rss.xml"
  ],
  "exclude_keywords": ["schalke ii", "u23"]
}
```

### Feed Health Alerts

If more than 50% of feeds fail during a refresh cycle (minimum 3 feeds polled), an alert is sent to `ADMIN_CHAT_ID` — once per day per club maximum.

---

## Shift Scheduler (`/dienstplan`)

A constraint-based 3-shift schedule generator for a nursing facility. Reads staff data, vacation, sick leave and shift wishes from Google Sheets.

### Shift Types

| Shift | Hours |
| :--- | :--- |
| Frühdienst (FD) | 7.5 h |
| Spätdienst (SD) | 7.0 h |
| Nachtdienst (ND) | 9.0 h |
| Frei, Urlaub, Krank, BT, Team, Supervision | — |

### Hard Constraints

| Rule | Description |
| :--- | :--- |
| H1 | Minimum staffing: ≥ 2 FD + 2 SD + 2 ND per day |
| H2 | No Spät → Früh (forbidden next-day assignment) |
| H3 | Night blocks: 3–4 consecutive nights |
| H4 | 2 mandatory free days after each night block |
| H5 | No other shifts inside an active night block |
| H6 | Ongoing night block must be completed (≥ 3 nights) |
| H7 | Max 5 consecutive working days |
| H8 | At least 1 full free weekend per employee per month |

### Soft Constraints

| Rule | Description |
| :--- | :--- |
| W1 | Equitable distribution of FD/SD/ND across all employees |
| W2 | Equitable night block distribution |
| W3 | Max 3 consecutive same-shift type (Pass 1) / 4 (Pass 2) |

### Multi-Agent Orchestration

```
MitarbeiterAgent  →  staff list + springer names
UrlaubAgent       →  vacation periods
KrankAgent        →  sick leave (Sheet + manual dialog input)
VormonatAgent     →  previous month plan (for block continuity)
WunschAgent       →  shift wishes (highest priority in Pass 1)
        │
        ▼
DienstplanGenerator
  Pass 1 — wish assignments (constraint-checked)
  Pass 2 — automatic assignments (soft constraints relaxed)
  Pass 3 — hour compensation (top-up underscheduled employees)
        │
        ▼
KontrollAgent  →  validate all hard + soft rules
        │
        ├─ violations found + rounds < 3 → iterate with tighter constraints
        └─ OK or max rounds reached → write to Google Sheets output
```

---

## Claude CLI Integration

### `/claude <prompt>` — Text Only

Spawns `claude --print --output-format text <prompt>` in the project root. Returns plain text. No tool execution. Timeout: 120 s.

**Use for**: explanations, code review, questions about the codebase.

### `/claudex <task>` — Full Agent

Spawns `claude --print --dangerouslySkipPermissions --output-format text <task>` in the project root. Claude can read/write files, run bash commands, execute git. Timeout: 300 s.

**Use for**: automated code changes, refactoring, writing tests, committing changes.

```
/claudex Read app/services/news_fetcher.py and explain the retry logic
/claudex Fix the type error in speech_to_text.py and commit the change
/claudex Write a unit test for claude_runner.py and add it to tests/
```

Both commands are guarded by the existing whitelist + rate limiter. No injection guard (the commands are intentionally a direct Claude prompt channel for trusted users).

---

## Security

All incoming messages pass through three layers before reaching any agent:

```
Whitelist → Rate Limiter → Injection Guard → Agent
```

### Whitelist

`ALLOWED_USER_IDS` — set of Telegram user IDs. Empty = all users blocked. Response: `⛔ Kein Zugriff.`

### Rate Limiter

Sliding window per `user_id`. Configurable via `RATE_LIMIT_MAX_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS`. Response: `⏳ Zu viele Nachrichten. Bitte {N}s warten.`

### Injection Guard (2-stage, async)

**Stage 1 — Pattern + Homoglyph** (instant, free):
- Hard-block regex patterns: `ignore.*instructions`, `you are now`, `jailbreak`, `dan mode`, `pretend you are`, `reveal.*system prompt`, etc.
- Soft-score patterns: `\bignore\b`, `\boverride\b`, `\bforget\b`, `\bsystem\b`, `\binstructions?\b`
- Homoglyph normalization: Cyrillic lookalike characters → ASCII before matching

**Stage 2 — LLM Guard** (only triggered when Stage 1 soft score > 0):
- Model: `anthropic/claude-haiku-4` via OpenRouter
- Timeout: 8 s
- Response `SAFE` → pass. Anything else or error → block.

Response: `⚠️ Ungültige Eingabe erkannt.`

---

## Memory System

Per-user, two-tier, JSON-persisted (`app/data/memory.json`).

### Confirmed Facts (`confirmed`)

Directly stated facts ("Mein Lieblingsverein ist Bayern") — extracted by LLM after every message and stored as key/value pairs (German lowercase keys). Used in agent system prompts and by the news agent to determine which clubs to fetch.

### Pending Facts (`pending`)

Inferred from context (indirect mentions). Each mention increments a counter. After **5 mentions** (`INDIRECT_THRESHOLD`) the fact is promoted to confirmed.

### Commands

| Command | Action |
| :--- | :--- |
| `/memory` | Show confirmed facts + pending facts with counters |
| `/forget` | Wipe all confirmed and pending facts |

---

## Strava Kudos Bot

Session-cookie based automation — no browser, no API key required.

### One-Time Setup

1. Log into Strava in a browser
2. DevTools → Application → Cookies → copy `_strava4_session` value
3. Run: `python strava_kudos/kudos_bot.py --set-session <VALUE>`

The session cookie is saved to `strava_kudos/session.json`.

### Via Telegram

`/strava` — the bot checks the session, fetches the following feed (30 activities), and gives Kudos to all activities not already liked. Returns a summary:

```
🏃 Strava Kudos – 08.05.2026 09:15
📄 Feed: 30 Aktivitäten
👍 Kudos gegeben: 7
⏭ Übersprungen: 23
🏅 Geliked:
  • Max Mustermann – Morgenrunde 10km
  • …
```

### Login Flow (Strava Next.js, 2025+)

Strava migrated to Next.js in 2025 with a 2-step login:

```
GET  /login               →  CSRF token from <meta name="csrf">
POST /session (Step 1)    →  email + auth_version=v2  →  otp_state
POST /session (Step 2)    →  password + otp_state     →  redirect_url
GET  redirect_url         →  session cookie set
GET  /dashboard/feed      →  following feed as JSON
POST /activities/{id}/kudos  →  give kudos
```

**Critical details**: CSRF header must be lowercase `x-csrf-token`. Body as `multipart/form-data`. `allow_redirects=False` on Step 2 POST.

---

## Stack

| Component | Purpose |
| :--- | :--- |
| `python-telegram-bot 22.x` | Telegram interface + JobQueue |
| `langgraph` | Multi-agent state machine |
| `langgraph-checkpoint-sqlite` | Async SQLite conversation checkpoints |
| `httpx` | Async HTTP client (feeds, OpenRouter, Tavily) |
| `openai-whisper` | Local voice transcription (lazy-loaded) |
| `pydub` | Audio conversion (OGG → WAV) |
| `matplotlib` | Chart rendering → PNG |
| `aiosqlite` | Async SQLite (news cache + LangGraph) |
| `gspread` | Google Sheets read/write |
| `tavily-python` | Web search |
| `python-dotenv` | .env config |
| OpenRouter | LLM backbone (routing, agents, enrichment, injection guard) |
| GNews API | News aggregation Layer 1 |
| Google Sheets | Schedule data I/O |
| Strava Web | Kudos automation |
| Claude Code CLI | `/claude` + `/claudex` via subprocess |

---

## Adding a New Agent

1. Create `app/agents/nodes/your_node.py` with `async def your_node(state: BotState) -> BotState`
2. Create `app/agents/your_agent.py` with the core logic
3. Register in `app/agents/graph.py` (`add_node`, `add_edge`, `add_conditional_edges`)
4. Add the new route name to the supervisor prompt in `app/agents/nodes/supervisor.py`

---

## Roadmap

- [x] LangGraph multi-agent architecture
- [x] LLM-based supervisor routing (confidence + topic-carry)
- [x] Football safety net (club + term → always football)
- [x] Persistent conversation memory (SQLite)
- [x] Rate limiting — sliding window
- [x] User whitelist
- [x] GitHub Actions CI
- [x] Chart Agent — renders PNG via Telegram
- [x] Per-user fact memory (confirmed + pending, threshold 5)
- [x] Football News Agent (`/news`, 4-layer aggregation)
- [x] GNews API + Google News RSS + static + club-specific feeds
- [x] LLM news enrichment (German headlines + snippets)
- [x] Jaccard clustering + source scoring + re-clustering
- [x] News cache (SQLite, 48h TTL, background refresh)
- [x] Daily news push (JobQueue, configurable time)
- [x] Feed health tracking + admin alerts (>50% failure)
- [x] Club config via `config/clubs.json` (aliases, feeds, exclude keywords)
- [x] Retry/Backoff for all RSS feeds (429/502/503, exponential)
- [x] Strava Kudos Bot (`/strava`, session-cookie auth)
- [x] Shift scheduler — 3-shift nursing schedule, 8 hard + 3 soft constraints
- [x] Schedule orchestrator — 5 sub-agents + KontrollAgent + multi-pass refinement
- [x] Google Sheets integration (staff, vacation, sick leave, wishes, output)
- [x] Claude Code CLI integration (`/claude`, `/claudex`)
- [ ] Football News fact-check + quality score
- [ ] Multi-language support (EN/DE toggle per user)
- [ ] Webhook mode (instead of polling) for lower latency

---

## License

Private project — not licensed for public use.
