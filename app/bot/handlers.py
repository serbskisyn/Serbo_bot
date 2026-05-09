import io
import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ContextTypes
from app.services.openrouter_client import extract_facts
from app.services.speech_to_text import transcribe_voice
from app.services.tts import synthesize as tts_synthesize
from app.security.injection_guard import is_injection_async
from app.security.rate_limiter import is_rate_limited
from app.bot.conversation import get_history, add_message, clear_history
from app.bot.memory import add_direct, add_indirect, clear_memory, format_memory_overview
from app.bot.whitelist import is_allowed
from app.agents.runner import run as agent_run
from app.agents.football_news_agent import fetch_news_for_user
from app.services.claude_runner import run_claude, run_claude_agent
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

# Punkt 3: Telegram-Retry-Schutz — bereits gesehene update_ids
_seen_update_ids: deque[int] = deque(maxlen=200)

# Punkt 7: HITL-Bestätigung für /claudex
_claudex_pending: dict[int, str] = {}


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
    facts = await extract_facts(text, response)
    for key, value in facts.get("direct", {}).items():
        add_direct(user_id, key, value)
    for fact in facts.get("indirect", []):
        add_indirect(user_id, fact)
    await update.message.reply_text(response)
    return response


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    clear_history(user.id)
    await update.message.reply_text(
        f"Hallo {user.first_name}! 👋\n"
        f"Ich bin dein KI-Assistent. Schreib mir einfach eine Nachricht.\n\n"
        f"/reset — Gesprächsverlauf löschen\n"
        f"/memory — Was ich über dich weiß\n"
        f"/forget — Mein Gedächtnis löschen\n"
        f"/news — Aktuelle News deiner Lieblingsclubs\n"
        f"/news fresh — News sofort neu laden (Live-Fetch)\n"
        f"/strava — Strava Kudos an alle Aktivitäten im Feed vergeben\n"
        f"/claude <Anfrage> — Claude Code CLI (nur Text)\n"
        f"/claudex <Aufgabe> — Claude Agent mit Tool-Zugriff (Dateien, Git)\n"
        f"/health — System-Status prüfen\n"
        f"/dienstplan — Dienstplan erstellen\n"
        f"/debugwunsch — Sheet-Struktur prüfen (Diagnose)\n\n"
        f"📅 *Kalender*\n"
        f"/termine [heute|morgen|woche] — Kalendertermine anzeigen\n"
        f"/kalender1 — Gmail-Kalender aktiv\n"
        f"/kalender2 — Workspace-Kalender aktiv"
    )


async def reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    clear_history(user_id)
    await update.message.reply_text("🗑️ Gesprächsverlauf gelöscht.")


async def memory_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    overview = format_memory_overview(user_id)
    await update.message.reply_text(overview)


async def forget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    clear_memory(user_id)
    await update.message.reply_text("🧹 Gedächtnis gelöscht.")


async def news_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

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


async def strava_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

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
        result = await asyncio.get_event_loop().run_in_executor(None, _run_kudos)
    except Exception as e:
        logger.exception("Strava Kudos Fehler")
        result = f"❌ Fehler beim Ausführen des Kudos-Bots: {e}"

    await update.message.reply_text(result)


async def claude_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    limited, retry_after = is_rate_limited(user_id)
    if limited:
        await update.message.reply_text(f"⏳ Zu viele Nachrichten. Bitte {retry_after}s warten.")
        return

    prompt = " ".join(context.args or []).strip()
    if not prompt:
        await update.message.reply_text("Verwendung: /claude <deine Anfrage>")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    result = await run_claude(prompt)

    for chunk in _split_message(result):
        await update.message.reply_text(chunk)


async def claudex_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Punkt 7: Zeigt HITL-Bestätigung bevor Claude mit vollen Tools läuft."""
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    limited, retry_after = is_rate_limited(user_id)
    if limited:
        await update.message.reply_text(f"⏳ Zu viele Nachrichten. Bitte {retry_after}s warten.")
        return

    prompt = " ".join(context.args or []).strip()
    if not prompt:
        await update.message.reply_text(
            "Verwendung: /claudex <Aufgabe>\n\nClaude hat vollen Tool-Zugriff: Dateien lesen/schreiben, Git, Bash."
        )
        return

    _claudex_pending[user_id] = prompt
    preview = prompt[:300] + ("…" if len(prompt) > 300 else "")
    await update.message.reply_text(
        f"🤖 *Claude Agent soll ausführen:*\n\n`{preview}`\n\n"
        "Bestätigen mit /ja — Abbrechen mit /nein",
        parse_mode="Markdown",
    )


async def ja_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return
    prompt = _claudex_pending.pop(user_id, None)
    if not prompt:
        await update.message.reply_text("Nichts zu bestätigen.")
        return
    await update.message.reply_text("🤖 Claude Agent läuft…")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    result = await run_claude_agent(prompt)
    for chunk in _split_message(result):
        await update.message.reply_text(chunk)


async def nein_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _claudex_pending.pop(user_id, None)
    await update.message.reply_text("❌ Abgebrochen.")


async def health_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    report = await run_health_check()
    await update.message.reply_text(report, parse_mode="Markdown")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text or ""
    logger.info("Textnachricht von User %d", user_id)

    # Punkt 3: Dedup
    uid = update.update_id
    if uid in _seen_update_ids:
        logger.debug("Doppelte update_id %d ignoriert", uid)
        return
    _seen_update_ids.append(uid)

    if not is_allowed(user_id):
        logger.warning("Unauthorized user | user=%d", user_id)
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    limited, retry_after = is_rate_limited(user_id)
    if limited:
        logger.warning("Rate limit exceeded | user=%d", user_id)
        await update.message.reply_text(f"⏳ Zu viele Nachrichten. Bitte {retry_after}s warten.")
        return

    # Punkt 2: Null-Byte-Strip + Längen-Limit
    user_text = user_text.replace("\x00", "").strip()
    if len(user_text) > MAX_INPUT_CHARS:
        await update.message.reply_text(f"⚠️ Nachricht zu lang (max {MAX_INPUT_CHARS} Zeichen).")
        return

    if await is_injection_async(user_text):
        logger.warning("Injection attempt | user=%d", user_id)
        await update.message.reply_text("⚠️ Ungültige Eingabe erkannt.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await _process_message(user_id, user_text, update, context)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info("Sprachnachricht von User %d", user_id)

    # Punkt 3: Dedup
    uid = update.update_id
    if uid in _seen_update_ids:
        logger.debug("Doppelte update_id %d ignoriert", uid)
        return
    _seen_update_ids.append(uid)

    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

    limited, retry_after = is_rate_limited(user_id)
    if limited:
        await update.message.reply_text(f"⏳ Zu viele Nachrichten. Bitte {retry_after}s warten.")
        return

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

    await update.message.reply_text(f"🎤 Transkribiert: _{text}_", parse_mode="Markdown")
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
    return "Kalender 1 (Gmail)" if cal_num == 1 else "Kalender 2 (Workspace)"


async def termine_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return

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
        loop = asyncio.get_event_loop()
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


async def kalender1_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    if not GCAL_CALENDAR_ID_1:
        await update.message.reply_text("❌ GCAL_CALENDAR_ID_1 nicht konfiguriert.")
        return
    set_active_calendar(user_id, 1)
    await update.message.reply_text("✅ Aktiver Kalender: *Kalender 1 (Gmail)*", parse_mode="Markdown")


async def kalender2_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Kein Zugriff.")
        return
    if not GCAL_CALENDAR_ID_2:
        await update.message.reply_text("❌ GCAL_CALENDAR_ID_2 nicht konfiguriert.")
        return
    set_active_calendar(user_id, 2)
    await update.message.reply_text("✅ Aktiver Kalender: *Kalender 2 (Workspace)*", parse_mode="Markdown")


async def error_handler(update, context):
    logger.error("Error: %s", context.error, exc_info=context.error)
