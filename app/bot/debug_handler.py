"""
debug_handler.py — /debugwunsch Befehl: zeigt alle Tabs + Roh-Daten des Wunsch-Sheets
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from app.config import SCHEDULE_WUNSCH_SHEET_ID

logger = logging.getLogger(__name__)


async def cmd_debugwunsch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔍 Lese Wunsch-Sheet-Struktur …")
    try:
        from app.services.gspread_client import debug_wunsch_sheet
        result = debug_wunsch_sheet(
            spreadsheet_id=SCHEDULE_WUNSCH_SHEET_ID,
            tab_name="Formularantworten 1",
            max_rows=3,
        )
        for chunk in _chunk(result, 3800):
            await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")
    except Exception as e:
        logger.exception("debugwunsch fehlgeschlagen")
        await update.message.reply_text(f"❌ Fehler: {e}")


def _chunk(text: str, max_len: int) -> list[str]:
    parts = []
    while len(text) > max_len:
        split = text.rfind("\n", 0, max_len)
        if split == -1:
            split = max_len
        parts.append(text[:split])
        text = text[split:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


def get_debug_handler() -> CommandHandler:
    return CommandHandler("debugwunsch", cmd_debugwunsch)
