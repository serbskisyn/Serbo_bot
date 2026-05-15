"""
HTTP-Client für die Trade Engine REST-API (Port 8081).
Alle /tradebot und /stocks Handler rufen ausschließlich diese Funktionen auf.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import TRADE_ENGINE_URL, TRADE_ENGINE_SECRET

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

_HEADERS = {"X-API-Secret": TRADE_ENGINE_SECRET}
_TIMEOUT = 10.0


async def _get(path: str) -> dict | list | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{TRADE_ENGINE_URL}{path}", headers=_HEADERS)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("Trade Engine GET %s fehlgeschlagen: %s", path, e)
        return None


async def _post(path: str, params: dict | None = None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(f"{TRADE_ENGINE_URL}{path}",
                                  headers=_HEADERS, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("Trade Engine POST %s fehlgeschlagen: %s", path, e)
        return None


# ── Formatierte Status-Reports ────────────────────────────────────────────────

async def fetch_crypto_status() -> str:
    data = await _get("/status")
    if not data:
        return "🔴 *Trade Engine offline* (Port 8081 nicht erreichbar)."

    crypto = data.get("crypto", {})
    stats  = data.get("stats", {})

    if not crypto.get("enabled"):
        return "⚠️ Kraken nicht konfiguriert."

    positions = crypto.get("positions", [])
    acc       = crypto.get("account", {})
    btc_bal   = float(acc.get("balance", 0)) if acc else None
    now       = datetime.now(ET).strftime("%d.%m.%Y %H:%M")

    lines = [
        f"🪙 *Crypto Trading — {now}*",
        f"🟢 Trade Engine läuft  |  Kraken 24/7",
    ]
    if btc_bal is not None:
        lines.append(f"💰 Kontostand: `{btc_bal:.6f} BTC`")
    lines.append("")

    lines.append(f"📈 *Offene Positionen: {len(positions)}*")
    for p in positions:
        entry    = float(p.get("entry_price", 0))
        side_raw = p.get("side", "long")
        side_tag = "🔴 SHORT" if side_raw == "short" else "🟢 LONG"
        lines.append(
            f"  • `{p['symbol']}` {side_tag} | Einstieg: `{entry:.6f}` | "
            f"Candles: {p.get('candles_held', 0)} | "
            f"Trailing: {'✅' if p.get('trailing_active') else '⏳'}"
        )

    if not positions:
        lines.append("  _Keine offenen Positionen_")

    lines += [
        "",
        f"📊 *Gesamt-Statistik*",
        f"Trades: `{stats.get('total_trades', 0)}` | "
        f"Win-Rate: `{stats.get('win_rate', 0):.1f}%`",
        f"Total P&L: `{'+' if stats.get('total_pl', 0) >= 0 else ''}"
        f"{stats.get('total_pl', 0):.6f} BTC`",
    ]
    return "\n".join(lines)


async def fetch_stocks_status() -> str:
    data = await _get("/status")
    if not data:
        return "⚠️ Trade Engine nicht erreichbar (Port 8081)."

    stocks = data.get("stocks", {})
    stats  = data.get("stats", {})

    if not stocks.get("enabled"):
        return "⚠️ Alpaca nicht konfiguriert."

    acc       = stocks.get("account", {})
    positions = stocks.get("positions", [])
    market_st = "🟢 Offen" if stocks.get("market_open") else "🔴 Geschlossen"
    mode      = "📄 Paper" if acc.get("mode") == "paper" else "💵 Live"
    equity    = float(acc.get("equity", 0))
    cash      = float(acc.get("cash", 0))
    day_pl    = equity - float(acc.get("last_equity", equity))
    day_sign  = "+" if day_pl >= 0 else ""
    now       = datetime.now(ET).strftime("%d.%m.%Y %H:%M ET")

    lines = [
        f"📊 *Alpaca — {now}*",
        f"Modus: {mode} | Markt: {market_st}\n",
        f"💰 *Konto*",
        f"Equity: `${equity:,.2f}`  |  Cash: `${cash:,.2f}`",
        f"Tages-P&L: `{day_sign}${day_pl:,.2f}`\n",
        f"📈 *Offene Positionen: {len(positions)}*",
    ]
    for p in positions:
        pl_pct = float(p.get("unrealized_plpc", 0)) * 100
        pl_abs = float(p.get("unrealized_pl", 0))
        icon   = "✅" if pl_pct >= 0 else "❌"
        sign   = "+" if pl_pct >= 0 else ""
        lines.append(f"  {icon} `{p['symbol']}`: `{sign}{pl_pct:.2f}%` ({sign}${pl_abs:.2f})")

    if not positions:
        lines.append("  _Keine offenen Positionen_")

    lines += [
        "",
        f"📊 *Trade Engine Statistik*",
        f"Trades: `{stats.get('total_trades', 0)}` | "
        f"Win-Rate: `{stats.get('win_rate', 0):.1f}%`",
        f"Total P&L: `{'+' if stats.get('total_pl', 0) >= 0 else ''}"
        f"${stats.get('total_pl', 0):.2f}`",
    ]
    return "\n".join(lines)


async def trigger_scan(market: str = "all") -> str:
    result = await _post("/scan", params={"market": market})
    if not result:
        return "⚠️ Trade Engine nicht erreichbar."
    return f"🔍 Scan gestartet (`{market}`) — Signale kommen per Push."
