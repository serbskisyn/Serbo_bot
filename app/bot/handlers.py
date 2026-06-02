import io
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ContextTypes
from app.services.profile_learner import learn as profile_learn
from app.services.todo_extractor import extract_from_chat as todo_extract
from app.services.completion_extractor import extract_from_chat as completion_extract
from app.services.todo_drop_extractor import extract_from_chat as todo_drop_extract
from app.services.context_extractor import extract_entities, extract_intents
from app.services.speech_to_text import transcribe_voice
from app.services.tts import synthesize as tts_synthesize
from app.security.injection_guard import is_injection_async
from app.security.rate_limiter import is_rate_limited
from app.bot.conversation import get_history, add_message, clear_history
from app.bot.memory import add_direct, add_indirect, clear_memory, format_memory_overview
from app.bot.whitelist import is_allowed, require_whitelist, guarded
from app.agents.runner import run as agent_run
from app.agents.football_news_agent import fetch_news_for_user
from app.agents.xnews_agent import fetch_x_news
from app.services.claude_runner import run_claude, run_claude_agent, run_claude_agent_continue, WORKDIR
from app.services.health_check import run_health_check
from app.bot.schedule_dialog import get_schedule_handler
from app.bot.debug_handler import get_debug_handler
from app.config import (
    TTS_ENABLED, TTS_VOICE,
    GCAL_CALENDAR_ID_1, GCAL_CALENDAR_ID_2,
)
from app.bot.gcal_state import get_active_calendar, set_active_calendar
from app.services.gcal_client import get_events, format_event
from strava_kudos.kudos_bot import (
    load_session_cookie, build_session, check_session, get_feed, give_kudos_to_feed
)

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 2000

# Claudex-Sessions: user_id → ursprüngliche Aufgabenbeschreibung
_claudex_sessions: dict[int, str] = {}


async def _typing_keepalive(bot, chat_id: int, interval: float = 4.0):
    """Sendet alle `interval` Sekunden eine Typing-Action bis die Task gecancelt wird."""
    while True:
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        await asyncio.sleep(interval)


async def _run_with_typing(bot, chat_id: int, coro):
    """Führt ein Coroutine aus und hält den Typing-Indikator am Leben."""
    keepalive = asyncio.create_task(_typing_keepalive(bot, chat_id))
    try:
        return await coro
    finally:
        keepalive.cancel()
        try:
            await keepalive
        except asyncio.CancelledError:
            pass


def _split_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    current = []
    current_len = 0
    for line in text.split("\n"):
        if current_len + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


async def _process_message(user_id: int, text: str, update: Update, context) -> str | None:
    history = get_history(user_id)
    result = await agent_run(user_id, text, history)

    if isinstance(result, dict) and result.get("response") == "__CHART__":
        png_bytes = result.get("chart_bytes")
        add_message(user_id, "user", text)
        add_message(user_id, "assistant", "[Chart generiert]")
        await update.message.reply_photo(photo=io.BytesIO(png_bytes))
        return None

    response = result if isinstance(result, str) else result.get("response", "")
    add_message(user_id, "user", text)
    add_message(user_id, "assistant", response)
    # Fire-and-forget the 3-stage profile learner + todo extractor +
    # completion extractor so we don't block the reply.
    asyncio.create_task(profile_learn(user_id, text, response))
    asyncio.create_task(todo_extract(user_id, text, response))
    asyncio.create_task(completion_extract(user_id, text, response))
    asyncio.create_task(todo_drop_extract(user_id, text, response))
    # Soft context layer (entities + relationship graph + soft intents)
    asyncio.create_task(extract_entities(user_id, text, response))
    asyncio.create_task(extract_intents(user_id, text))
    await update.message.reply_text(response)
    return response


@require_whitelist
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    clear_history(user.id)
    await update.message.reply_text(
        f"Hallo {user.first_name}! 👋\n"
        f"Schreib mir einfach eine Nachricht.\n\n"
        f"🌅 *Tagesrhythmus*\n"
        f"/briefing — Morgen-Digest manuell (auto 06:30)\n"
        f"/reflect — Tagesabschluss (auto 21:30)\n"
        f"/summary — Chat-Zusammenfassung (auto 23:00)\n\n"
        f"✅ *Todos*\n"
        f"/todo — Heute fällige Todos\n"
        f"/todo `add <text> [heute|morgen|freitag|30.05]`\n"
        f"/todo `list [today|week|all]`\n"
        f"/todo `done|snooze|drop|show <id>` · `stats`\n\n"
        f"📅 *Kalender*\n"
        f"/termine `[heute|morgen|woche]`\n"
        f"/kalender1 · /kalender2 — Aktiven Kalender wählen\n\n"
        f"📈 *Trading*\n"
        f"/tradebot — Status (Crypto + Stocks)\n"
        f"/papertrade — 🧪 Dry-Run Status\n"
        f"/recap `[Tage]` — Backtest + Live-Pulse (default 7)\n"
        f"/tradebot `crypto pause|resume` · `stocks scan` · `help`\n\n"
        f"🌐 *Recherche*\n"
        f"/news `[fresh]` — Football News\n"
        f"/xnews `<thema>` — X.com Live via Grok\n"
        f"/strava — Kudos an Feed\n\n"
        f"🛠 *System*\n"
        f"/claude `<anfrage>` — Claude Code CLI\n"
        f"/claudex `<task>` — Agent-Session (Dateien, Git, Bash)\n"
        f"  └ /fertig `[commit]` · /nein\n"
        f"/health · /tests\n\n"
        f"🧠 *Memory*\n"
        f"/memory · /forget · /reset\n"
        f"/curator `[run|apply|cancel]` — Profil aufräumen\n\n"
        f"🏥 *Goldkind*\n"
        f"/dienstplan — Dienstplan erstellen\n"
        f"/debugwunsch — Sheet-Diagnose\n\n"
        f"🏢 *Atolls*\n"
        f"/leads `[rerun <zeile>]` — Lead-Qualifying",
        parse_mode="Markdown",
    )


@require_whitelist
async def reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_history(user_id)
    await update.message.reply_text("🗑️ Gesprächsverlauf gelöscht.")


@require_whitelist
async def memory_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    overview = format_memory_overview(user_id)
    await update.message.reply_text(overview)


@require_whitelist
async def forget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await clear_memory(user_id)
    await update.message.reply_text("🧹 Gedächtnis gelöscht.")


@require_whitelist
async def xnews_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    topic = " ".join(args).strip()
    if not topic:
        await update.message.reply_text(
            "ℹ️ Nutzung: `/xnews <thema>`\n"
            "Beispiel: `/xnews Bitcoin ETF Genehmigung`",
            parse_mode="Markdown",
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    reply = await _run_with_typing(
        context.bot,
        update.effective_chat.id,
        fetch_x_news(topic),
    )
    for chunk in _split_message(reply):
        await update.message.reply_text(
            chunk,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )


@require_whitelist
async def news_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args or []
    force_refresh = any(a.lower() == "fresh" for a in args)

    if force_refresh:
        await update.message.reply_text("🔄 Lade News live neu — einen Moment…")
    else:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    blocks: list[str] = await fetch_news_for_user(user_id, force_refresh=force_refresh)

    for block in blocks:
        for chunk in _split_message(block):
            await update.message.reply_text(
                chunk,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )


@require_whitelist
async def strava_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("🏃 Starte Strava Kudos-Bot…")

    def _run_kudos() -> str:
        from datetime import datetime
        cookie = load_session_cookie()
        if not cookie:
            return (
                "❌ Kein Session-Cookie gefunden.\n"
                "Einmalig ausführen:\n"
                "  python kudos_bot.py --set-session <_strava4_session-Cookie-Wert>"
            )
        session = build_session(cookie)
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        if not check_session(session):
            return (
                f"🔒 Session abgelaufen ({ts}).\n"
                "Cookie erneuern:\n"
                "  python kudos_bot.py --set-session <neuer_cookie_wert>"
            )
        entries = get_feed(session)
        total = len(entries)
        if not entries:
            return f"🏃 Strava Kudos – {ts}\n\n📢 Feed leer – nichts Neues."
        given, skipped, errors, names = give_kudos_to_feed(session, entries)
        lines = [
            f"🏃 Strava Kudos – {ts}",
            "",
            f"📄 Feed: {total} Aktivitäten",
            f"👍 Kudos gegeben: {given}",
            f"⏭ Übersprungen: {skipped}",
        ]
        if errors:
            lines.append(f"❌ Fehler: {errors}")
        if names:
            lines.append("")
            lines.append("🏅 Geliked:")
            for n in names[:10]:
                lines.append(f"  • {n}")
            if len(names) > 10:
                lines.append(f"  … und {len(names) - 10} weitere")
        return "\n".join(lines)

    try:
        result = await asyncio.get_running_loop().run_in_executor(None, _run_kudos)
    except Exception as e:
        logger.exception("Strava Kudos Fehler")
        result = f"❌ Fehler beim Ausführen des Kudos-Bots: {e}"

    await update.message.reply_text(result)


@require_whitelist
async def claude_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    limited, retry_after = is_rate_limited(user_id)
    if limited:
        await update.message.reply_text(f"⏳ Zu viele Nachrichten. Bitte {retry_after}s warten.")
        return

    prompt = " ".join(context.args or []).strip()
    if not prompt:
        await update.message.reply_text("Verwendung: /claude <deine Anfrage>")
        return

    result = await _run_with_typing(
        context.bot, update.effective_chat.id, run_claude(prompt)
    )
    for chunk in _split_message(result):
        await update.message.reply_text(chunk)


@require_whitelist
async def claudex_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    limited, retry_after = is_rate_limited(user_id)
    if limited:
        await update.message.reply_text(f"⏳ Zu viele Nachrichten. Bitte {retry_after}s warten.")
        return

    prompt = " ".join(context.args or []).strip()
    if not prompt:
        if user_id in _claudex_sessions:
            task = _claudex_sessions[user_id]
            await update.message.reply_text(
                f"🤖 *Session aktiv:* _{task[:120]}_\n\n"
                "Weitere Nachrichten direkt eingeben.\n"
                "/fertig — Session beenden\n"
                "/fertig commit — beenden + committen\n"
                "/nein — abbrechen",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "Verwendung: /claudex <Aufgabe>\n\n"
                "Claude hat vollen Tool-Zugriff (Dateien, Git, Bash).\n"
                "Folgenachrichten setzen die Session fort.\n"
                "/fertig [commit] beendet sie."
            )
        return

    _claudex_sessions[user_id] = prompt
    await update.message.reply_text("🤖 Claude Agent startet…")
    result = await _run_with_typing(
        context.bot, update.effective_chat.id, run_claude_agent(prompt)
    )
    for chunk in _split_message(result):
        await update.message.reply_text(chunk)
    await update.message.reply_text(
        "💬 *Session aktiv* — weitere Nachrichten direkt eingeben.\n"
        "/fertig · /fertig commit · /nein",
        parse_mode="Markdown",
    )


async def claudex_fertig_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return

    task = _claudex_sessions.pop(user_id, None)
    if task is None:
        await update.message.reply_text("Keine aktive Claudex-Session.")
        return

    do_commit = "commit" in [a.lower() for a in (context.args or [])]
    if not do_commit:
        await update.message.reply_text("✅ Claudex-Session beendet.")
        return

    await update.message.reply_text("📦 Committe Änderungen…")

    proc_add = await asyncio.create_subprocess_exec(
        "git", "add", "-A",
        cwd=str(WORKDIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc_add.communicate()

    commit_msg = f"claudex: {task[:72]}"
    proc_commit = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", commit_msg,
        cwd=str(WORKDIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc_commit.communicate()
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")

    if proc_commit.returncode == 0:
        first_line = out.strip().split("\n")[0]
        await update.message.reply_text(f"✅ Committed: `{first_line}`", parse_mode="Markdown")
    elif "nothing to commit" in out or "nothing to commit" in err:
        await update.message.reply_text("✅ Session beendet — keine Änderungen zu committen.")
    else:
        await update.message.reply_text(f"⚠️ Commit fehlgeschlagen:\n{err.strip()[:400]}")


@require_whitelist
async def nein_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if _claudex_sessions.pop(user_id, None) is not None:
        await update.message.reply_text("❌ Claudex-Session abgebrochen.")
    else:
        await update.message.reply_text("❌ Abgebrochen.")


@require_whitelist
async def health_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    report = await run_health_check()
    await update.message.reply_text(report, parse_mode="Markdown")


@require_whitelist
async def tests_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import asyncio, subprocess
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    async def _run_suite(label: str, cwd: str, venv: str) -> str:
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [f"{venv}/bin/pytest", "tests/", "-v", "--tb=short", "--no-header"],
                cwd=cwd, capture_output=True, text=True, timeout=120,
            )
        )
        output = (result.stdout + result.stderr).strip()
        # Letzte Zusammenfassungszeile extrahieren
        lines = output.splitlines()
        summary = next((l for l in reversed(lines) if "passed" in l or "failed" in l or "error" in l), "kein Ergebnis")
        # Nur fehlgeschlagene Tests + Summary ausgeben, bei Grün nur Summary
        if result.returncode != 0:
            failed_lines = [l for l in lines if "FAILED" in l or "ERROR" in l or "ERRORS" in l]
            detail = "\n".join(failed_lines[:20])
            return f"*{label}*\n❌ {summary}\n```\n{detail}\n```"
        return f"*{label}*\n✅ {summary}"

    serbo   = await _run_suite("Serbo\\_bot", "/home/pi/Serbo_bot",    "/home/pi/Serbo_bot/.venv")
    trade   = await _run_suite("Trade Engine", "/home/pi/trade_engine", "/home/pi/trade_engine/.venv")
    report  = f"🧪 *Test-Report* — {__import__('datetime').datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n{serbo}\n\n{trade}"
    for chunk in _split_message(report):
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def _leads_rerun_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    args: list[str],
) -> None:
    """Re-verarbeitet einen einzelnen Lead anhand seiner Sheet-Zeile."""
    import time as _time
    chat_id = update.effective_chat.id

    if len(args) < 2 or not args[1].isdigit():
        await update.message.reply_text(
            "⚠️ Verwendung: `/leads rerun <Zeile>` — z.B. `/leads rerun 90`",
            parse_mode="Markdown",
        )
        return

    target_row = int(args[1])
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await update.message.reply_text(
        f"🔄 *Rerun Zeile {target_row}* — lese Sheet …",
        parse_mode="Markdown",
    )

    try:
        from app.agents.lead_qualifying.services.sheets import (
            read_inbound_leads,
            write_validation_for_row,
        )
        from app.agents.lead_qualifying.graph import _per_lead_graph
        from app.agents.lead_qualifying.nodes.fetch_new_leads import _compute_lead_key
        from app.agents.lead_qualifying.nodes.write_results import write_results_node
        from app.agents.lead_qualifying.state import LeadState

        rows = await read_inbound_leads()
        lead = next((r for r in rows if r.get("_row_index") == target_row), None)
        if lead is None:
            await update.message.reply_text(
                f"❌ Zeile `{target_row}` nicht im Inbound-Tab gefunden.",
                parse_mode="Markdown",
            )
            return

        firma = lead.get("Firma") or "(unbekannt)"
        await update.message.reply_text(
            f"▶️ Verarbeite *{firma}* (Zeile {target_row}) …\n"
            "Pro Lead ~45 s (Perplexity + Pepper-Subprocess).",
            parse_mode="Markdown",
        )

        # Reset idempotency guard so write_results writes a fresh Validation_Date
        await write_validation_for_row(target_row, {"Validation_Date": ""})
        lead["_lead_key"] = _compute_lead_key(lead)

        lead_state: LeadState = {
            "raw_leads": [], "new_leads": [], "processed_leads": [], "errors": [],
            "current_lead": lead,
            "pre_qualify_label": "", "pre_qualify_reason": "",
            "contact_title": "", "linkedin_url": "", "company_website": "",
            "northdata_summary": "", "news_summary": "",
            "discovered_brands": [], "is_holding": False, "validated_brands": [],
            "company_revenue": "", "company_employees": "", "company_hq": "",
            "primary_markets": [], "business_model": "", "sales_signals": "",
            "target_country_iso": "",
            "pepper_by_brand": {}, "pepper_brands_found": 0,
            "pepper_total_mentions_all": 0,
            "pepper_target_summary": "", "pepper_cross_summary": "", "pepper_summary": "",
            "business_fit_shoop": "", "business_fit_igraal": "",
            "business_fit_mydealz": "", "business_fit_gutscheine": "",
            "score_total": 0, "classification": "", "recommended_action": "",
            "_employee_count_estimate": "",
            "contact_authority": "other", "contact_role_match": False,
        }

        t0 = _time.monotonic()
        result = await _per_lead_graph.ainvoke(lead_state)
        final  = await write_results_node(result)
        elapsed = _time.monotonic() - t0

        clf    = result.get("classification", "—")
        score  = result.get("score_total", 0)
        pepper = result.get("pepper_summary") or "—"
        errors = final.get("errors", [])

        lines = [
            f"✅ *Rerun Zeile {target_row} fertig* ({elapsed:.0f} s)",
            f"Firma: `{firma}` — {clf} (Score: {score}/100)",
            f"Pepper: {pepper}",
        ]
        if errors:
            lines.append(f"⚠️ Fehler: {errors[0][:200]}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as exc:
        logger.error("leads rerun: Fehler Zeile %d: %s", target_row, exc, exc_info=True)
        await update.message.reply_text(
            f"❌ *Rerun fehlgeschlagen*\n\n`{exc}`",
            parse_mode="Markdown",
        )


@require_whitelist
async def leads_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manuell den Lead-Qualifying-Agent triggern.

    /leads              — verarbeitet bis zu LEAD_QUALIFYING_MAX_PER_RUN neue Leads
    /leads <N>          — verarbeitet genau N Leads (Override)
    /leads rerun <Zeile> — einzelnen Lead neu verarbeiten (Validation_Date wird zurückgesetzt)
    """
    import time as _time
    chat_id = update.effective_chat.id

    # /leads rerun <row_index>
    args = (context.args or [])
    if args and args[0].lower() == "rerun":
        await _leads_rerun_handler(update, context, args)
        return

    # /leads <N> — run exactly N leads
    max_leads_override: int | None = None
    if args and args[0].isdigit():
        max_leads_override = int(args[0])

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    if max_leads_override is not None:
        await update.message.reply_text(
            f"🚀 *Lead-Qualifying gestartet* — verarbeite genau `{max_leads_override}` Lead(s). "
            "Pro Lead ~45 s (Perplexity + Pepper-Subprocess).",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "🚀 *Lead-Qualifying gestartet* — verarbeite bis zu "
            f"`{__import__('os').getenv('LEAD_QUALIFYING_MAX_PER_RUN', '30')}` Leads. "
            "Pro Lead ~45 s (Perplexity + Pepper-Subprocess).",
            parse_mode="Markdown",
        )

    t0 = _time.monotonic()
    try:
        from app.agents.lead_qualifying.graph import run_pipeline
        final_state = await run_pipeline(max_leads=max_leads_override)
    except Exception as exc:
        logger.error("leads_handler: Pipeline-Fehler: %s", exc, exc_info=True)
        await update.message.reply_text(
            f"❌ *Lead-Qualifying fehlgeschlagen*\n\nFehler: `{exc}`",
            parse_mode="Markdown",
        )
        return

    elapsed = _time.monotonic() - t0
    processed = final_state.get("processed_leads", [])
    errors = final_state.get("errors", [])

    if not processed:
        await update.message.reply_text(
            f"✅ *Lead-Qualifying durch* — keine neuen Leads gefunden ({elapsed:.0f} s).",
            parse_mode="Markdown",
        )
        return

    qualified = [d for d in processed if d.get("classification") != "FILTERED"]
    filtered = len(processed) - len(qualified)
    lines = [
        f"✅ *Lead-Qualifying durch* ({elapsed:.0f} s)",
        f"`{len(processed)}` Leads — `{len(qualified)}` qualifiziert, `{filtered}` gefiltert",
    ]
    if errors:
        lines.append(f"⚠️ `{len(errors)}` Fehler im Run")

    # Kompakter Detail-Block pro Lead
    for i, d in enumerate(qualified[:5], 1):
        name  = f"{d.get('vorname', '')} {d.get('nachname', '')}".strip() or "(unbekannt)"
        firma = d.get("firma", "")
        clf   = d.get("classification", "")
        pep   = (d.get("_pepper_summary") or "").split(" · ")[0]
        groesse = d.get("_employee_count") or "—"
        lines.append(f"\n*{i}.* `{name}` @ `{firma[:30]}` — {clf}")
        lines.append(f"   Größe: {groesse} | Pepper: {pep}")

    if len(qualified) > 5:
        lines.append(f"\n_…und {len(qualified) - 5} weitere._")

    lines.append(
        f"\n📊 Validierungsspalten im Inbound-Sheet "
        f"(Spalten L-Q) aktualisiert."
    )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@guarded
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text or ""
    logger.info("Textnachricht von User %d", user_id)

    # Punkt 2: Null-Byte-Strip + Längen-Limit
    user_text = user_text.replace("\x00", "").strip()
    if len(user_text) > MAX_INPUT_CHARS:
        await update.message.reply_text(f"⚠️ Nachricht zu lang (max {MAX_INPUT_CHARS} Zeichen).")
        return

    if await is_injection_async(user_text):
        logger.warning("Injection attempt | user=%d", user_id)
        await update.message.reply_text("⚠️ Ungültige Eingabe erkannt.")
        return

    # Aktive Claudex-Session: Nachricht direkt an Claude Agent weiterleiten
    if user_id in _claudex_sessions:
        result = await _run_with_typing(
            context.bot, update.effective_chat.id, run_claude_agent_continue(user_text)
        )
        for chunk in _split_message(result):
            await update.message.reply_text(chunk)
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await _process_message(user_id, user_text, update, context)


@guarded
async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info("Sprachnachricht von User %d", user_id)

    voice = update.message.voice
    voice_file = await context.bot.get_file(voice.file_id)
    voice_bytes = await voice_file.download_as_bytearray()

    try:
        text = await transcribe_voice(bytes(voice_bytes))
    except Exception as e:
        logger.exception("Transkription fehlgeschlagen")
        await update.message.reply_text(f"❌ Transkription fehlgeschlagen: {e}")
        return

    if not text:
        await update.message.reply_text("❌ Transkription leer.")
        return

    # Punkt 2: Längen-Limit
    text = text.replace("\x00", "").strip()
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]

    if await is_injection_async(text):
        logger.warning("Injection attempt (voice) | user=%d", user_id)
        await update.message.reply_text("⚠️ Ungültige Eingabe erkannt.")
        return

    await update.message.reply_text(f"🎤 Transkribiert: {text}")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Punkt 5: TTS — Antwort generieren und als Audio zurückschicken
    response = await _process_message(user_id, text, update, context)
    if TTS_ENABLED and response:
        audio_bytes = await tts_synthesize(response, voice=TTS_VOICE)
        if audio_bytes:
            await update.message.reply_audio(
                audio=io.BytesIO(audio_bytes),
                filename="antwort.mp3",
                read_timeout=30,
                write_timeout=30,
            )


_BERLIN = ZoneInfo("Europe/Berlin")
_WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONTHS_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def _cal_id(cal_num: int) -> str:
    return GCAL_CALENDAR_ID_1 if cal_num == 1 else GCAL_CALENDAR_ID_2


def _cal_label(cal_num: int) -> str:
    return "Kalender 1 (Benno@atolls.com)" if cal_num == 1 else "Kalender 2 (Bennoschwede@gmail.com)"


@require_whitelist
async def termine_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args or []
    mode = args[0].lower() if args else "heute"

    now = datetime.now(_BERLIN)
    if mode == "morgen":
        day_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        mode_label = "morgen"
    elif mode == "woche":
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=7)
        mode_label = "diese Woche"
    else:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        mode_label = "heute"

    cal_num = get_active_calendar(user_id)
    calendar_id = _cal_id(cal_num)

    if not calendar_id:
        await update.message.reply_text(
            "❌ Kein Kalender konfiguriert.\n"
            "GCAL_CALENDAR_ID_1 (und optional GCAL_CALENDAR_ID_2) in .env setzen.\n"
            "Vorher: Kalender in Google Calendar mit serbo-bot@goldkind.iam.gserviceaccount.com teilen."
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        loop = asyncio.get_running_loop()
        start_utc = day_start.astimezone(timezone.utc)
        end_utc = day_end.astimezone(timezone.utc)
        events = await loop.run_in_executor(
            None, get_events, calendar_id, start_utc, end_utc
        )
    except FileNotFoundError as e:
        await update.message.reply_text(f"❌ {e}")
        return
    except Exception as e:
        logger.exception("Kalender-Fehler")
        await update.message.reply_text(f"❌ Fehler beim Abrufen: {e}")
        return

    label = _cal_label(cal_num)
    if not events:
        await update.message.reply_text(
            f"📅 Keine Termine {mode_label}.\n_{label}_",
            parse_mode="Markdown",
        )
        return

    dow = _WEEKDAYS_DE[day_start.weekday()]
    date_str = f"{dow}, {day_start.day}. {_MONTHS_DE[day_start.month - 1]} {day_start.year}"
    heading = f"📅 *Termine {mode_label}*"
    if mode != "woche":
        heading += f" — {date_str}"
    heading += f"\n_{label}_\n\n"

    lines = [format_event(e) for e in events]
    await update.message.reply_text(heading + "\n\n".join(lines), parse_mode="Markdown")


@require_whitelist
async def kalender1_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not GCAL_CALENDAR_ID_1:
        await update.message.reply_text("❌ GCAL_CALENDAR_ID_1 nicht konfiguriert.")
        return
    set_active_calendar(user_id, 1)
    await update.message.reply_text("✅ Aktiver Kalender: *Kalender 1 (Benno@atolls.com)*", parse_mode="Markdown")


@require_whitelist
async def kalender2_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not GCAL_CALENDAR_ID_2:
        await update.message.reply_text("❌ GCAL_CALENDAR_ID_2 nicht konfiguriert.")
        return
    set_active_calendar(user_id, 2)
    await update.message.reply_text("✅ Aktiver Kalender: *Kalender 2 (Bennoschwede@gmail.com)*", parse_mode="Markdown")


async def error_handler(update, context):
    logger.error("Error: %s", context.error, exc_info=context.error)
