import logging
from datetime import datetime

import httpx

from app.config import FREQTRADE_API_URL, FREQTRADE_API_USERNAME, FREQTRADE_API_PASSWORD

logger = logging.getLogger(__name__)

_TOKEN_CACHE: dict = {}
_BOT_PAUSED: bool = False  # lokales Flag, da Freqtrade /stopbuy keinen eigenen State hat


async def _get_token(client: httpx.AsyncClient) -> str:
    cached = _TOKEN_CACHE.get("token")
    if cached:
        return cached
    r = await client.post(
        f"{FREQTRADE_API_URL}/api/v1/token/login",
        auth=(FREQTRADE_API_USERNAME, FREQTRADE_API_PASSWORD),
    )
    r.raise_for_status()
    token = r.json()["access_token"]
    _TOKEN_CACHE["token"] = token
    return token


def _invalidate_token() -> None:
    _TOKEN_CACHE.clear()


async def _get(client: httpx.AsyncClient, path: str) -> dict | list:
    token = await _get_token(client)
    r = await client.get(
        f"{FREQTRADE_API_URL}/api/v1{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if r.status_code == 401:
        _invalidate_token()
        token = await _get_token(client)
        r = await client.get(
            f"{FREQTRADE_API_URL}/api/v1{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
    r.raise_for_status()
    return r.json()


async def _post(client: httpx.AsyncClient, path: str) -> dict:
    token = await _get_token(client)
    r = await client.post(
        f"{FREQTRADE_API_URL}/api/v1{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if r.status_code == 401:
        _invalidate_token()
        token = await _get_token(client)
        r = await client.post(
            f"{FREQTRADE_API_URL}/api/v1{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
    r.raise_for_status()
    return r.json()


_COMMANDS = {
    "pause":  ("/stopbuy",  "⏸ Neue Käufe pausiert — offene Trades laufen weiter."),
    "resume": ("/start",    "▶️ Bot läuft wieder — neue Käufe aktiv."),
    "stop":   ("/stop",     "🛑 Bot vollständig gestoppt."),
    "start":  ("/start",    "▶️ Bot gestartet."),
}

_PAUSE_CMDS  = {"pause"}
_RESUME_CMDS = {"resume", "start"}


async def send_bot_command(cmd: str) -> str:
    global _BOT_PAUSED
    entry = _COMMANDS.get(cmd.lower())
    if not entry:
        available = ", ".join(f"`{k}`" for k in _COMMANDS)
        return f"❓ Unbekannter Befehl. Verfügbar: {available}"
    path, success_msg = entry
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            data = await _post(client, path)
        if cmd in _PAUSE_CMDS:
            _BOT_PAUSED = True
        elif cmd in _RESUME_CMDS:
            _BOT_PAUSED = False
        status = data.get("status", "")
        return f"{success_msg}\nFreqtrade: `{status}`" if status else success_msg
    except Exception as e:
        logger.warning("Freqtrade Steuerung fehlgeschlagen: %s", e)
        return "⚠️ Trading Bot nicht erreichbar."


async def _fetch_bot_state(client: httpx.AsyncClient) -> str:
    try:
        data = await _get(client, "/show_config")
        raw = str(data.get("state", "unknown")).lower()
        if raw == "stopped":
            return "stopped"
        return "paused" if _BOT_PAUSED else "running"
    except Exception:
        return "unknown"


async def fetch_trading_status() -> str:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            state, status, profit, balance, trades = await _fetch_all(client)
        return _format_report(state, status, profit, balance, trades)
    except Exception as e:
        logger.warning("Freqtrade API nicht erreichbar: %s", e)
        return "⚠️ Trading Bot nicht erreichbar. Läuft er noch?\n`sudo systemctl status trading_bot`"


async def _fetch_all(client: httpx.AsyncClient) -> tuple:
    import asyncio
    state_t   = _fetch_bot_state(client)
    status_t  = _get(client, "/status")
    profit_t  = _get(client, "/profit")
    balance_t = _get(client, "/balance")
    trades_t  = _get(client, "/trades?limit=5")
    return await asyncio.gather(state_t, status_t, profit_t, balance_t, trades_t)


def _fmt_pct(val) -> str:
    val = float(val or 0) * 100
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


def _fmt_profit(val) -> str:
    val = float(val or 0)
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.4f}"


_STATE_LABEL = {
    "running": "🟢 Läuft",
    "paused":  "⏸ Pausiert (kein Kauf)",
    "stopped": "🛑 Gestoppt",
    "unknown": "❓ Unbekannt",
}


def _fmt_amount(val: float, currency: str) -> str:
    decimals = 8 if currency == "BTC" else 2
    return f"{val:.{decimals}f} {currency}"


def _format_report(state: str, status: list, profit: dict, balance: dict, trades: dict) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    state_label = _STATE_LABEL.get(state, state)
    lines = [f"📊 *Trading Bot — {now}*", f"Status: {state_label}\n"]

    # Wallet — stake currency dynamisch aus API
    stake_currency = balance.get("stake", balance.get("symbol", "USDT"))
    total = float(balance.get("total", 0))
    currencies = balance.get("currencies", [])
    stake_entry = next((c for c in currencies if c.get("currency") == stake_currency), {})
    free = float(stake_entry.get("free", total))
    lines.append(f"💰 *Wallet* ({stake_currency})")
    lines.append(f"Gesamt: `{_fmt_amount(total, stake_currency)}`  |  Frei: `{_fmt_amount(free, stake_currency)}`\n")

    # Offene Trades
    lines.append(f"📈 *Offene Trades:* {len(status)}")
    for t in status:
        pair = t.get("pair", "?")
        pnl = float(t.get("profit_pct", 0))
        sign = "+" if pnl >= 0 else ""
        dur = t.get("open_date", "")[:16]
        lines.append(f"  • {pair}: `{sign}{pnl:.2f}%` seit {dur}")
    lines.append("")

    # Profit-Zusammenfassung
    trade_count = int(profit.get("trade_count", 0))
    win_count   = int(profit.get("winning_trades", 0))
    loss_count  = int(profit.get("losing_trades", 0))
    win_pct     = (win_count / trade_count * 100) if trade_count else 0
    total_pnl   = float(profit.get("profit_all_coin", 0))
    total_pnl_pct = float(profit.get("profit_all_percent_mean", 0)) * 100
    best        = float(profit.get("best_pair_profit_percent", 0)) * 100
    best_pair   = profit.get("best_pair", "—")

    total_fiat     = float(profit.get("profit_closed_fiat", 0))
    fiat_sign      = "+" if total_fiat >= 0 else ""
    sign_total     = "+" if total_pnl >= 0 else ""
    lines.append(f"📋 *Gesamt P&L*")
    lines.append(f"Trades: `{trade_count}` | Gewinner: `{win_count}` ({win_pct:.0f}%) | Verlierer: `{loss_count}`")
    lines.append(f"Profit: `{sign_total}{_fmt_amount(total_pnl, stake_currency)}` (`{fiat_sign}{total_fiat:.2f} EUR`)")
    if best_pair and best_pair != "—":
        lines.append(f"Bestes Pair: `{best_pair}` ({'+' if best >= 0 else ''}{best:.2f}%)")
    lines.append("")

    # Letzte abgeschlossene Trades
    closed = trades.get("trades", [])
    if closed:
        lines.append(f"🕐 *Letzte {min(len(closed), 5)} Trades*")
        for t in closed[-5:][::-1]:
            pair    = t.get("pair", "?")
            pnl_pct = float(t.get("profit_pct", 0))
            pnl_abs = float(t.get("profit_abs", 0))
            icon    = "✅" if pnl_pct >= 0 else "❌"
            sign    = "+" if pnl_pct >= 0 else ""
            dur_h   = int(t.get("trade_duration_s", 0) // 3600)
            lines.append(
                f"  {icon} {pair}: `{sign}{pnl_pct:.2f}%` "
                f"({sign}{_fmt_amount(pnl_abs, stake_currency)}, {dur_h}h)"
            )

    return "\n".join(lines)
