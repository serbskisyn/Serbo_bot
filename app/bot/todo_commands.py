"""
todo_commands.py — /todo Telegram command router.

Subcommands:
    /todo                          → list "today" view
    /todo add <text> [date]        → add a new todo
    /todo list [today|week|all]    → list todos in scope
    /todo done <id>                → mark done
    /todo drop <id>                → drop without doing
    /todo snooze <id> <days>       → snooze N days
    /todo show <id>                → details for one todo
    /todo stats                    → counts per status

Dates accepted: heute, morgen, übermorgen, freitag, 30.05[.2026], 2026-05-30
"""
from __future__ import annotations

import logging
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.whitelist import require_whitelist
from app.services import todos as todos_svc


def _meeting_line(notes: str | None) -> str:
    """Render the meeting-context sub-line for a granola todo, or ''."""
    ctx = todos_svc.parse_meeting_context(notes)
    if not ctx:
        return ""
    title, iso = ctx
    try:
        d = date.fromisoformat(iso)
        date_str = d.strftime("%d.%m.")
    except Exception:
        date_str = iso
    return f"\n   ↳ _Meeting: {title} ({date_str})_"

logger = logging.getLogger(__name__)


_HELP = (
    "*📝 ToDo-Befehle*\n\n"
    "`/todo` — heute fällig\n"
    "`/todo add <text> [datum]` — neu anlegen\n"
    "`/todo list [today|week|all]` — Liste\n"
    "`/todo done <id>` — erledigt\n"
    "`/todo drop <id>` — verwerfen\n"
    "`/todo snooze <id> <tage>` — vertagen\n"
    "`/todo show <id>` — Details\n"
    "`/todo stats` — Übersicht\n\n"
    "_Datums-Hints:_ `heute`, `morgen`, `übermorgen`, `freitag`, `30.05`, `2026-05-30`"
)


def _fmt_due(due: str | None) -> str:
    if not due:
        return ""
    try:
        d = date.fromisoformat(due)
        today = date.today()
        delta = (d - today).days
        if delta == 0:
            return "📅 heute"
        if delta == 1:
            return "📅 morgen"
        if delta < 0:
            return f"⚠️ überfällig ({-delta}d)"
        if delta <= 7:
            return f"📅 in {delta}d ({d.strftime('%a')})"
        return f"📅 {d.strftime('%d.%m.')}"
    except Exception:
        return f"📅 {due}"


def _fmt_todo_row(t: dict) -> str:
    due = _fmt_due(t.get("due_date"))
    mentions = int(t.get("mention_count") or 1)
    badge = ""
    if mentions >= 3:
        badge = f" · 🔥 {mentions}x"
    source = t.get("source")
    src_badge = ""
    if source == "granola":
        src_badge = " · 🗣"
    elif source == "gcal":
        src_badge = " · 🗓"
    elif source == "chat":
        src_badge = " · 💬"
    head = f"*#{t['id']}* {t['text']}" + (f" — {due}" if due else "") + badge + src_badge
    return head + _meeting_line(t.get("notes"))


@require_whitelist
async def todo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = context.args or []
    if not args:
        return await _cmd_list(update, user_id, "today")

    sub = args[0].lower()
    rest = args[1:]

    if sub == "add":
        return await _cmd_add(update, user_id, rest)
    if sub in ("list", "ls"):
        scope = (rest[0].lower() if rest else "today")
        return await _cmd_list(update, user_id, scope)
    if sub == "done":
        return await _cmd_done(update, user_id, rest)
    if sub == "drop":
        return await _cmd_drop(update, user_id, rest)
    if sub == "snooze":
        return await _cmd_snooze(update, user_id, rest)
    if sub == "show":
        return await _cmd_show(update, user_id, rest)
    if sub == "stats":
        return await _cmd_stats(update, user_id)
    if sub in ("help", "h", "?"):
        return await update.message.reply_text(_HELP, parse_mode="Markdown")

    # Unknown subcommand → treat the whole arg list as a quick-add
    return await _cmd_add(update, user_id, args)


async def _cmd_add(update: Update, user_id: int, parts: list[str]) -> None:
    if not parts:
        return await update.message.reply_text(
            "Was soll ich notieren? Beispiel: `/todo add Deck vorbereiten morgen`",
            parse_mode="Markdown",
        )
    raw = " ".join(parts).strip()
    text, due = todos_svc._extract_trailing_date(raw)
    text = text.strip() or raw  # fallback if extract returned empty

    existing = await todos_svc.mention_existing(user_id, text)
    if existing:
        return await update.message.reply_text(
            f"🔁 Schon offen als *#{existing}* — Mention-Count erhöht.",
            parse_mode="Markdown",
        )

    new_id = await todos_svc.add_todo(user_id, text, due_date=due)
    due_str = f" ({_fmt_due(due)})" if due else ""
    await update.message.reply_text(
        f"✅ *#{new_id}* notiert: {text}{due_str}",
        parse_mode="Markdown",
    )


async def _cmd_list(update: Update, user_id: int, scope: str) -> None:
    if scope not in ("today", "week", "all"):
        scope = "today"
    rows = await todos_svc.list_todos(user_id, scope=scope)
    if not rows:
        scope_label = {"today": "heute", "week": "diese Woche", "all": "insgesamt"}[scope]
        return await update.message.reply_text(
            f"🎉 Nichts offen für *{scope_label}*.",
            parse_mode="Markdown",
        )
    scope_label = {"today": "Heute", "week": "Diese Woche", "all": "Alle offen"}[scope]
    lines = [f"*📝 {scope_label}* ({len(rows)})\n"]
    for t in rows[:20]:
        lines.append("• " + _fmt_todo_row(t))
    if len(rows) > 20:
        lines.append(f"\n… und {len(rows) - 20} weitere")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _cmd_done(update: Update, user_id: int, parts: list[str]) -> None:
    if not parts:
        return await update.message.reply_text("Welche ID? `/todo done 42`", parse_mode="Markdown")
    try:
        todo_id = int(parts[0].lstrip("#"))
    except ValueError:
        return await update.message.reply_text("ID muss eine Zahl sein.")
    if await todos_svc.mark_done(user_id, todo_id):
        await update.message.reply_text(f"✅ *#{todo_id}* erledigt!", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Kein offenes Todo mit ID #{todo_id}.")


async def _cmd_drop(update: Update, user_id: int, parts: list[str]) -> None:
    if not parts:
        return await update.message.reply_text("Welche ID? `/todo drop 42`", parse_mode="Markdown")
    try:
        todo_id = int(parts[0].lstrip("#"))
    except ValueError:
        return await update.message.reply_text("ID muss eine Zahl sein.")
    if await todos_svc.drop_todo(user_id, todo_id):
        await update.message.reply_text(f"🗑 *#{todo_id}* verworfen.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ ID #{todo_id} nicht gefunden.")


async def _cmd_snooze(update: Update, user_id: int, parts: list[str]) -> None:
    if len(parts) < 2:
        return await update.message.reply_text(
            "Usage: `/todo snooze <id> <tage>`",
            parse_mode="Markdown",
        )
    try:
        todo_id = int(parts[0].lstrip("#"))
        days = int(parts[1])
    except ValueError:
        return await update.message.reply_text("ID und Tage müssen Zahlen sein.")
    until = await todos_svc.snooze_todo(user_id, todo_id, days)
    if until:
        await update.message.reply_text(
            f"😴 *#{todo_id}* vertagt bis {until}.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(f"❌ ID #{todo_id} konnte nicht vertagt werden.")


async def _cmd_show(update: Update, user_id: int, parts: list[str]) -> None:
    if not parts:
        return await update.message.reply_text("Welche ID? `/todo show 42`", parse_mode="Markdown")
    try:
        todo_id = int(parts[0].lstrip("#"))
    except ValueError:
        return await update.message.reply_text("ID muss eine Zahl sein.")
    t = await todos_svc.get_todo(user_id, todo_id)
    if not t:
        return await update.message.reply_text(f"❌ ID #{todo_id} nicht gefunden.")
    lines = [
        f"*#{t['id']}* — _{t['status']}_",
        t["text"],
        "",
    ]
    if t.get("due_date"):
        lines.append(f"Fällig: {_fmt_due(t['due_date'])}")
    if t.get("snoozed_until"):
        lines.append(f"Vertagt bis: {t['snoozed_until']}")
    lines.append(f"Quelle: {t['source']}")
    lines.append(f"Erwähnungen: {t.get('mention_count', 1)}")
    if t.get("notes"):
        lines.append(f"\n_{t['notes']}_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _cmd_stats(update: Update, user_id: int) -> None:
    s = await todos_svc.stats(user_id)
    msg = (
        f"*📊 ToDo-Übersicht*\n\n"
        f"• Offen: {s['open']}\n"
        f"• Vertagt: {s['snoozed']}\n"
        f"• Erledigt: {s['done']}\n"
        f"• Verworfen: {s['dropped']}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
