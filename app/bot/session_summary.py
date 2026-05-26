import logging
from datetime import date
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.conversation import get_history
from app.bot.memory import get_confirmed
from app.bot.whitelist import require_whitelist
from app.config import (
    ALLOWED_USER_IDS, SESSION_SUMMARY_MIN_MESSAGES,
    ADMIN_CHAT_ID,
)
from app.services.openrouter_client import ask_llm
from app.bot.bot_context import get_bot

logger = logging.getLogger(__name__)

_SUMMARY_DIR = Path(__file__).parent.parent / "data" / "summaries"

_SUMMARY_PROMPT = """Du erstellst eine kompakte Gesprächszusammenfassung.
Fasse das heutige Gespräch in maximal 5 Bullet Points zusammen.
Schreibe NUR neue Informationen, die noch nicht im Nutzerprofil stehen.
Wenn es nichts Neues gibt, antworte mit: (nichts Neues heute)
Sei präzise, kein Fülltext. Deutsch."""


async def _summarize_user(user_id: int) -> str | None:
    history = get_history(user_id)
    if len(history) < SESSION_SUMMARY_MIN_MESSAGES:
        return None

    confirmed = get_confirmed(user_id)
    profile_str = "\n".join(f"- {k}: {v}" for k, v in confirmed.items()) or "(leer)"
    conversation = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in history[-30:]
    )

    prompt = (
        f"Nutzerprofil (bereits bekannt):\n{profile_str}\n\n"
        f"Heutiges Gespräch:\n{conversation}"
    )
    summary = await ask_llm(prompt, system_prompt=_SUMMARY_PROMPT)
    if "(nichts Neues heute)" in summary:
        return None
    return summary


async def create_daily_summaries(context=None) -> None:
    _SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    bot = get_bot()

    for user_id in ALLOWED_USER_IDS:
        out_file = _SUMMARY_DIR / f"{user_id}_{today}.md"
        if out_file.exists():
            continue
        try:
            summary = await _summarize_user(user_id)
            if not summary:
                continue
            out_file.write_text(
                f"# Gesprächszusammenfassung {today}\n\n{summary}\n",
                encoding="utf-8",
            )
            logger.info("Session Summary gespeichert: %s", out_file.name)
            if bot and ADMIN_CHAT_ID:
                await bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"📋 *Tageszusammenfassung {today}*\n\n{summary}",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.warning("Session Summary für user %d fehlgeschlagen: %s", user_id, e)


@require_whitelist
async def summary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual trigger — show the current user's session summary now.

    Mirrors the scheduled 23:00 job but only for the requesting user, and
    bypasses the "already exists" file-skip so it can be regenerated.
    """
    user_id = update.effective_user.id
    await update.message.reply_text("⏳ Tageszusammenfassung wird erstellt …")
    try:
        summary = await _summarize_user(user_id)
    except Exception as exc:
        logger.error("summary_handler: %s", exc, exc_info=True)
        await update.message.reply_text("❌ Zusammenfassung fehlgeschlagen.")
        return

    if not summary:
        await update.message.reply_text(
            f"_Heute noch nichts Substantielles im Chat — "
            f"mindestens {SESSION_SUMMARY_MIN_MESSAGES} Nachrichten nötig._",
            parse_mode="Markdown",
        )
        return

    today = date.today().isoformat()
    _SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    out_file = _SUMMARY_DIR / f"{user_id}_{today}.md"
    try:
        out_file.write_text(
            f"# Gesprächszusammenfassung {today}\n\n{summary}\n",
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("summary_handler: write failed: %s", exc)

    await update.message.reply_text(
        f"📋 *Tageszusammenfassung {today}*\n\n{summary}",
        parse_mode="Markdown",
    )
