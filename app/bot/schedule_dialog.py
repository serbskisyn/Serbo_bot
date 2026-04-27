"""
schedule_dialog.py — Telegram ConversationHandler für /dienstplan
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters,
)

from app.services.schedule_builder import (
    DienstplanGenerator,
    Mitarbeiter,
    Abwesenheit,
    Wunschschicht,
)
from app.config import (
    SCHEDULE_URLAUB_SHEET_ID,
    SCHEDULE_WUNSCH_SHEET_ID,
    SCHEDULE_KRANK_SHEET_ID,
    SCHEDULE_OUTPUT_SHEET_ID,
)

logger = logging.getLogger(__name__)

MONAT, KRANKTAGE, BESTAETIGUNG = range(3)

# Fallback falls Sheet nicht ladbar
MITARBEITER_FALLBACK: dict[str, float] = {
    "Heike":     7.0,
    "Silke":     8.0,
    "Ariane":    7.0,
    "Jasmin":    7.0,
    "Maria":     7.0,
    "Linus":     7.0,
    "Celina":    6.0,
    "Geraldine": 8.0,
    "Svitlana":  7.0,
    "Elvira":    7.0,
    "Romy":      6.0,
    "Annika":    7.0,  # 35h / 5
}

MONATE_DE = {
    "januar": 1, "februar": 2, "maerz": 3, "märz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}


def _parse_monat(text: str) -> tuple[int, int] | None:
    text = text.strip().lower()
    for name, num in MONATE_DE.items():
        if name in text:
            m = re.search(r"(20\d{2})", text)
            jahr = int(m.group(1)) if m else datetime.now().year
            return num, jahr
    m = re.match(r"(\d{1,2})[./\s]+(20\d{2})", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _parse_kranktage(text: str) -> list[Abwesenheit]:
    result = []
    lines = re.split(r"[,\n]", text)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        name = parts[0].capitalize()
        rest = " ".join(parts[1:])
        pattern = r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?[\s\-bis]+(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?"
        m = re.search(pattern, rest)
        if m:
            d1, m1, y1, d2, m2, y2 = m.groups()
            jahr = int(y1) if y1 else datetime.now().year
            try:
                start = date(jahr, int(m1), int(d1))
                end   = date(int(y2) if y2 else jahr, int(m2), int(d2))
                current = start
                while current <= end:
                    result.append(Abwesenheit(name=name, art="K", datum=current))
                    current += timedelta(days=1)
            except ValueError:
                pass
        else:
            m = re.search(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", rest)
            if m:
                d1, m1, y1 = m.groups()
                try:
                    datum = date(int(y1) if y1 else datetime.now().year, int(m1), int(d1))
                    result.append(Abwesenheit(name=name, art="K", datum=datum))
                except ValueError:
                    pass
    return result


async def cmd_dienstplan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📅 *Dienstplan-Generator*\n\n"
        "Für welchen Monat soll der Plan erstellt werden?\n"
        "Beispiel: `Mai 2026` oder `05/2026`",
        parse_mode="Markdown",
    )
    return MONAT


async def handle_monat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    result = _parse_monat(update.message.text)
    if not result:
        await update.message.reply_text(
            "❌ Format nicht erkannt. Bitte eingeben wie: `Mai 2026` oder `5/2026`",
            parse_mode="Markdown",
        )
        return MONAT
    monat, jahr = result
    context.user_data["monat"] = monat
    context.user_data["jahr"]  = jahr
    monate_namen = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
                    "Juli", "August", "September", "Oktober", "November", "Dezember"]
    await update.message.reply_text(
        f"✅ Monat: *{monate_namen[monat]} {jahr}*\n\n"
        "Gibt es manuelle Krankmeldungen (zusätzlich zum Sheet)?\n"
        "Format: `Name Von-Bis` z.B. `Maria 03.05-07.05`\n"
        "Mehrere: je eine Zeile oder Komma getrennt.\n\n"
        "Ohne manuelle Eingabe: `/fertig`",
        parse_mode="Markdown",
    )
    context.user_data["kranktage"] = []
    return KRANKTAGE


async def handle_kranktage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() in ("/fertig", "fertig", "keine"):
        return await _starte_generierung(update, context)
    kranktage = _parse_kranktage(text)
    if not kranktage:
        await update.message.reply_text(
            "❌ Format nicht erkannt.\nBeispiel: `Maria 03.05-07.05` oder `/fertig`",
            parse_mode="Markdown",
        )
        return KRANKTAGE
    context.user_data["kranktage"].extend(kranktage)
    namen = list({k.name for k in kranktage})
    await update.message.reply_text(
        f"✅ {len(kranktage)} Kranktage für {', '.join(namen)} gespeichert.\n"
        "Weitere Krankmeldungen oder `/fertig`.",
        parse_mode="Markdown",
    )
    return KRANKTAGE


def _build_wunsch_notizen(
    generator: DienstplanGenerator,
) -> dict[str, list[tuple[date, str, bool]]]:
    notizen: dict[str, list[tuple[date, str, bool]]] = {}
    for ma_name, wuensche in generator._wunsch_index.items():
        for wdatum, wdienst in wuensche:
            geplant  = generator.plan.get(ma_name, {}).get(wdatum)
            erfuellt = (geplant == wdienst)
            notizen.setdefault(ma_name, []).append(
                (wdatum, wdienst.value, erfuellt)
            )
    return notizen


async def _starte_generierung(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    monat     = context.user_data["monat"]
    jahr      = context.user_data["jahr"]
    kranktage: list[Abwesenheit] = context.user_data.get("kranktage", [])

    await update.message.reply_text("⏳ Lade Mitarbeiter, Urlaubsdaten, Krankenstand und Wunschschichten …")

    # ── Mitarbeiter aus Sheet laden ───────────────────────────────────
    ma_liste: list[Mitarbeiter] = []
    try:
        from app.services.gspread_client import read_mitarbeiter
        ma_liste = read_mitarbeiter(SCHEDULE_OUTPUT_SHEET_ID, "Mitarbeiterübersicht")
        namen_str = ", ".join(ma.name for ma in ma_liste)
        await update.message.reply_text(
            f"👥 {len(ma_liste)} Mitarbeiter geladen: {namen_str}"
        )
    except Exception as e:
        logger.warning("Mitarbeiterliste nicht ladbar (%s) — Fallback aktiv", e)
        await update.message.reply_text(
            f"⚠️ Mitarbeiterliste nicht ladbar: {e}\nNutze Fallback-Liste."
        )
        ma_liste = [
            Mitarbeiter(name=name, tagesstunden=std)
            for name, std in MITARBEITER_FALLBACK.items()
        ]

    bekannte_namen = {ma.name for ma in ma_liste}

    abwesenheiten: list[Abwesenheit] = list(kranktage)

    # ── Urlaub ──────────────────────────────────────────────────────
    try:
        from app.services.gspread_client import read_abwesenheiten
        ab_urlaub = read_abwesenheiten(SCHEDULE_URLAUB_SHEET_ID, "Urlaub_CLI")
        abwesenheiten.extend(ab_urlaub)
        logger.info("Urlaub geladen: %d Einträge", len(ab_urlaub))
    except Exception as e:
        logger.warning("Urlaub laden fehlgeschlagen: %s", e)
        await update.message.reply_text(f"⚠️ Urlaubsdaten nicht ladbar: {e}")

    # ── Krankenstand aus Sheet ───────────────────────────────────────
    if SCHEDULE_KRANK_SHEET_ID:
        try:
            from app.services.gspread_client import read_krankenstand
            ab_krank = read_krankenstand(SCHEDULE_KRANK_SHEET_ID, "Krankenstand")
            abwesenheiten.extend(ab_krank)
            if ab_krank:
                namen_krank = list({a.name for a in ab_krank})
                await update.message.reply_text(
                    f"🤒 Krankenstand geladen: {len(ab_krank)} Tage "
                    f"({', '.join(namen_krank)})"
                )
            else:
                await update.message.reply_text("ℹ️ Kein Krankenstand im Sheet eingetragen.")
        except Exception as e:
            logger.warning("Krankenstand laden fehlgeschlagen: %s", e)
            await update.message.reply_text(f"⚠️ Krankenstand nicht ladbar: {e}")

    # ── Wunschschichten ────────────────────────────────────────────────
    wunschschichten: list[Wunschschicht] = []
    try:
        from app.services.gspread_client import read_wunschschichten
        wunschschichten = read_wunschschichten(
            spreadsheet_id=SCHEDULE_WUNSCH_SHEET_ID,
            tab_name="Formularantworten 1",
            monat=monat,
            jahr=jahr,
            bekannte_namen=bekannte_namen,
        )
        if wunschschichten:
            # Eindeutige Personen mit Wünschen
            personen = list({w.name for w in wunschschichten})
            await update.message.reply_text(
                f"🙋 {len(wunschschichten)} Wunschschichten von "
                f"{len(personen)} Personen geladen: {', '.join(personen)}"
            )
        else:
            await update.message.reply_text("ℹ️ Keine Wunschschichten für diesen Monat gefunden.")
    except Exception as e:
        logger.warning("Wunschschichten laden fehlgeschlagen: %s", e)
        await update.message.reply_text(f"⚠️ Wunschschichten nicht ladbar: {e}")

    # ── Plan generieren ────────────────────────────────────────────────
    try:
        gen = DienstplanGenerator(
            mitarbeiter_liste=ma_liste,
            abwesenheiten=abwesenheiten,
            jahr=jahr,
            monat=monat,
            wunschschichten=wunschschichten,
        )
        plan   = gen.generate()
        report = gen.get_report()
        for chunk in _chunk_text(report, 4000):
            await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")
        context.user_data["gen"]  = gen
        context.user_data["plan"] = plan
    except Exception as e:
        logger.exception("Generierung fehlgeschlagen")
        await update.message.reply_text(f"❌ Fehler bei der Planerstellung: {e}")
        return ConversationHandler.END

    keyboard = [["✅ In Google Sheets übertragen", "❌ Abbrechen"]]
    await update.message.reply_text(
        "Plan erstellt. In Google Sheets übertragen?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return BESTAETIGUNG


async def handle_bestaetigung(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if "Abbrechen" in update.message.text:
        await update.message.reply_text("Plan verworfen.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    await update.message.reply_text("⏳ Schreibe in Google Sheets …", reply_markup=ReplyKeyboardRemove())
    try:
        from app.services.gspread_client import write_dienstplan
        gen  = context.user_data["gen"]
        plan = context.user_data["plan"]
        wunsch_notizen = _build_wunsch_notizen(gen)
        tab = write_dienstplan(
            spreadsheet_id=SCHEDULE_OUTPUT_SHEET_ID,
            plan=plan,
            mitarbeiter=[ma.name for ma in gen.ma_liste],
            tage=gen.tage,
            wunsch_notizen=wunsch_notizen,
        )
        await update.message.reply_text(
            f"✅ Dienstplan in Tab *{tab}* geschrieben!\n"
            f"https://docs.google.com/spreadsheets/d/{SCHEDULE_OUTPUT_SHEET_ID}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("Sheets schreiben fehlgeschlagen")
        await update.message.reply_text(f"❌ Fehler: {e}")
    return ConversationHandler.END


async def cmd_abbrechen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Abgebrochen.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def _chunk_text(text: str, max_len: int = 4000) -> list[str]:
    chunks = []
    while len(text) > max_len:
        split = text.rfind("\n", 0, max_len)
        if split == -1:
            split = max_len
        chunks.append(text[:split])
        text = text[split:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def get_schedule_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("dienstplan", cmd_dienstplan)],
        states={
            MONAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_monat),
            ],
            KRANKTAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_kranktage),
                CommandHandler("fertig", handle_kranktage),
            ],
            BESTAETIGUNG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bestaetigung),
            ],
        },
        fallbacks=[CommandHandler("abbrechen", cmd_abbrechen)],
        name="dienstplan_dialog",
        persistent=False,
    )
