# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
python -m app.main          # start bot
pytest tests/ -v            # run all tests
pytest tests/test_injection_guard.py -v   # run a single test file
```

System dependency: `ffmpeg` must be installed for voice transcription (pydub → Whisper).

## Architecture

The bot is a Telegram-facing multi-agent system. Every incoming message passes through three security layers before reaching any agent:

```
User (Telegram)
  → Whitelist (bot/whitelist.py)
  → Rate Limiter (security/rate_limiter.py)
  → Injection Guard (security/injection_guard.py)  ← two-stage: pattern + LLM
  → LangGraph Runner (agents/runner.py)
       └── Supervisor node  →  general | football | chart | web
  → Fact extraction → memory.py
  → Reply sent to Telegram
```

**LangGraph state machine** (`agents/graph.py`, `agents/state.py`):
- `BotState` TypedDict carries: `user_id`, `text`, `agent`, `response`, `messages`, `chart_bytes`, `topic`, `confidence`
- `supervisor.py` routes via LLM JSON classification (not keywords); confidence + topic carry for ambiguous follow-ups (`CONFIDENCE_THRESHOLD = 0.60`)
- Conversation history is checkpointed in `app/data/conversation.db` via `AsyncSqliteSaver`, keyed by `user_id` as `thread_id`
- Chart responses are signalled by returning `"__CHART__"` as the response string; the runner converts PNG bytes to a Telegram photo

**Memory** (`bot/profile.py`, `bot/memory.py` shim):
- Structured per-user YAML profile at `app/data/profile.yaml` (identity/work/interests/preferences/people/projects/goals/facts/pending/archived). `bot/memory.py` is a backwards-compat shim over it.
- Facts are extracted asynchronously after every message via the 3-stage learner (`services/profile_learner.py`: detect → write → review), applied through `profile.apply_ops()`.

**Jarvis memory layer** (FabBot-inspired personal-context augmentation):
- **Curator** (`services/curator.py`, `bot/curator_job.py`): periodic profile consolidation. LLM proposes merges/archival as a dry-run → user confirms `/curator apply`. Archive-not-delete (into `profile.archived`), `_pinned` dict-items are never touched, proposals carry the profile hash they were based on and are refused if the profile changed since. Scheduled daily (cooldown-gated to ~weekly).
- **Recall-loop** (`services/notes_index.py`): embeds `app/data/summaries/*.md` into the semantic.db `notes` collection (mtime-gated, idempotent ref_ids) and the `general` node recalls the top-K relevant past chunks. Closes the old write-only gap where reflections were never read back.
- **Soft context layer** (`services/context_store.py` + `context_extractor.py`): fire-and-forget extraction of entities (person/place/event/task) and soft intents into `app/data/context.db`, with a co-occurrence `context_links` table forming a relationship graph. Deliberately does NOT auto-create todos (the todo_extractor owns that). Dedup is exact + whole-token-prefix on the name.
- **Proactive context** (`services/proactive_context.py`): merges open today/overdue todos + mention-weighted soft items into a keyword-deduped block injected into the `general` node prompt, so the bot can surface relevant items mid-conversation.

**News pipeline** (`/news` command):
1. `football_news_agent.py` reads favourite clubs from user memory
2. `news_cache.py` serves SQLite-cached articles (48 h TTL, background refresh every 45 ± 15 min)
3. `news_fetcher.py` pulls from GNews API → Google News RSS → static RSS feeds → club-specific Transfermarkt feeds
4. `news_ranker.py` clusters duplicates via Jaccard similarity (threshold 0.25), scores by source count
5. `news_enricher.py` asks the LLM to write a German headline + 50-word snippet per article
6. LLM deduplication selects top-5 diverse articles per club

**Schedule builder** (`services/schedule_builder.py`, `bot/schedule_dialog.py`):
- `DienstplanGenerator` is a constraint solver for 3-shift nursing schedules (Früh/Spät/Nacht)
- Staff data and vacation/sick data come from Google Sheets via `gspread_client.py`
- Surfaced through a 3-step Telegram `ConversationHandler`

## Key design decisions

- **Language**: bot responds in German; all system prompts are German
- **LLM**: OpenRouter with configurable model (`OPENROUTER_MODEL`). Default `openai/gpt-4o-mini`. Injection guard Stage 2 uses `claude-haiku-4` via OpenRouter
- **Routing**: LLM-based (supervisor node), not keyword-based — the old `router.py` is no longer in use
- **Config**: all env values are loaded once in `app/config.py` and imported from there — never call `os.getenv()` in other modules
- **Handlers**: all async; shared text+voice processing lives in `_process_message()` — don't duplicate it
- **Injection guard**: always call `is_injection_async()` (async two-stage), never the sync wrapper, inside async handlers

## Adding a new agent

1. Create `app/agents/your_agent.py` with agent logic
2. Create `app/agents/nodes/your_node.py` as an async LangGraph node function (`async def your_node(state: BotState) -> BotState`)
3. Register the node in `agents/graph.py` (`graph.add_node`, `graph.add_edge`)
4. Add the new route name to the supervisor prompt in `agents/nodes/supervisor.py`

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | required | Bot token from @BotFather |
| `OPENROUTER_API_KEY` | required | OpenRouter API key |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | LLM model identifier |
| `TAVILY_API_KEY` | required | Tavily web search key |
| `GNEWS_API_KEY` | `""` | GNews API key (optional) |
| `ALLOWED_USER_IDS` | `""` | Comma-separated Telegram user IDs whitelist |
| `RATE_LIMIT_MAX_REQUESTS` | `10` | Max messages per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit window (seconds) |
| `NEWS_CACHE_MAX_AGE_HOURS` | `48` | News cache TTL |
| `NEWS_SCHEDULER_BASE_MINUTES` | `45` | Background cache refresh interval |
| `NEWS_DAILY_PUSH_HOUR` | `6` | Daily push time (hour, CEST) |
| `NEWS_DAILY_PUSH_MINUTE` | `30` | Daily push time (minute) |
| `NEWS_DAILY_PUSH_USER_IDS` | `""` | Users receiving daily news push |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | `""` | Google Sheets service account credentials |
| `SCHEDULE_OUTPUT_SHEET_ID` | (hardcoded) | Google Sheet ID for schedule output |
| `BOT_NAME` | `MeinAgent` | Bot display name |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `CURATOR_ENABLED` | `true` | Enable weekly profile consolidation |
| `CURATOR_HOUR` / `CURATOR_MINUTE` | `4` / `30` | Scheduled curator dry-run time (CEST) |
| `CURATOR_COOLDOWN_DAYS` | `7` | Min days between scheduled curator runs |
| `CURATOR_PROPOSAL_TTL_HOURS` | `24` | How long a pending proposal stays valid |
| `RECALL_ENABLED` | `true` | Enable semantic recall of past summaries |
| `RECALL_TOP_K` | `3` | Recalled note chunks injected per query |
| `SOFT_LAYER_ENABLED` | `true` | Enable entity/intent/graph extraction |
| `SOFT_PROMOTION_MENTIONS` | `2` | Mentions before a soft item surfaces |
| `PROACTIVE_CONTEXT_ENABLED` | `true` | Inject open-items block into chat prompt |
| `PROACTIVE_MAX_ITEMS` | `6` | Max items in the proactive context block |

## Adding club news feeds

Add an entry to `CLUB_FEEDS` in `app/services/news_fetcher.py`:

```python
"fc schalke 04": [
    "https://www.transfermarkt.de/fc-schalke-04/rss/verein/33",
    "https://www.reviersport.de/rss.xml",
],
```
