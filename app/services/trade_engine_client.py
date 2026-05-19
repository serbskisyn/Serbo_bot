"""
HTTP-Client für die Trade Engine REST-API (Port 8081).
Alle /tradebot und /stocks Handler rufen ausschließlich diese Funktionen auf.
"""
import asyncio
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

async def _btc_to_eur() -> float:
    """Fetch current BTC/EUR rate from Trade Engine's Kraken connection."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("https://api.kraken.com/0/public/Ticker?pair=XBTEUR")
            r.raise_for_status()
            result = r.json().get("result", {})
            pair = next(iter(result.values()))
            return float(pair["c"][0])
    except Exception:
        return 0.0


async def fetch_status() -> str:
    """Kombinierter Status: Kraken Crypto + Alpaca Stocks in einer Ansicht."""
    data, recent = await asyncio.gather(_get("/status"), _get("/trades/recent?limit=5"))
    if not data:
        return "🔴 *Trade Engine offline* (Port 8081 nicht erreichbar)."

    crypto = data.get("crypto", {})
    stocks = data.get("stocks", {})
    stats  = data.get("stats", {})
    cb     = data.get("circuit_breaker", {})
    btc_eur = await _btc_to_eur()
    now    = datetime.now(ET).strftime("%d.%m.%Y %H:%M ET")
    lines  = [f"🤖 *Trading Bot — {now}*"]

    # ── Kraken Crypto ─────────────────────────────────────────────────────────
    lines.append("")
    if crypto.get("enabled"):
        acc     = crypto.get("account", {})
        btc_bal = float(acc.get("balance", 0)) if acc else None
        c_pos   = crypto.get("positions", [])
        eur_s   = f" (~{btc_bal * btc_eur:,.2f} €)" if btc_bal and btc_eur else ""
        bal_s   = f"`{btc_bal:.6f} BTC`{eur_s}" if btc_bal is not None else "–"
        lines.append(f"🪙 *Kraken Crypto* | 24/7")
        lines.append(f"💰 {bal_s}")
        lines.append(f"📈 Positionen: *{len(c_pos)}*")
        for p in c_pos:
            entry   = float(p.get("entry_price", 0))
            s_tag   = "🔴 SHORT" if p.get("side") == "short" else "🟢 LONG"
            lines.append(
                f"  • `{p['symbol']}` {s_tag} | `{entry:.8f}` | "
                f"{p.get('candles_held', 0)} Candles | "
                f"Trail: {'✅' if p.get('trailing_active') else '⏳'}"
            )
        if not c_pos:
            lines.append("  _Keine offenen Positionen_")
    else:
        lines.append("🪙 Kraken nicht konfiguriert")

    # ── Alpaca Stocks ─────────────────────────────────────────────────────────
    lines.append("")
    if stocks.get("enabled"):
        acc     = stocks.get("account", {})
        s_pos   = stocks.get("positions", [])
        equity  = float(acc.get("equity", 0))
        cash    = float(acc.get("cash", 0))
        day_pl  = equity - float(acc.get("last_equity", equity))
        d_sign  = "+" if day_pl >= 0 else ""
        m_st    = "🟢 Offen" if stocks.get("market_open") else "🔴 Geschlossen"
        mode    = "Paper" if acc.get("mode") == "paper" else "Live"
        lines.append(f"📈 *Alpaca Stocks* | {m_st} | {mode}")
        lines.append(f"💰 Equity: `${equity:,.2f}` | Cash: `${cash:,.2f}` | Tag: `{d_sign}${day_pl:,.2f}`")
        lines.append(f"📈 Positionen: *{len(s_pos)}*")
        for p in s_pos:
            pl_pct = float(p.get("unrealized_plpc", 0)) * 100
            pl_abs = float(p.get("unrealized_pl", 0))
            icon   = "✅" if pl_pct >= 0 else "❌"
            sign   = "+" if pl_pct >= 0 else ""
            lines.append(f"  {icon} `{p['symbol']}` `{sign}{pl_pct:.2f}%` ({sign}${pl_abs:.2f})")
        if not s_pos:
            lines.append("  _Keine offenen Positionen_")
    else:
        lines.append("📈 Alpaca nicht konfiguriert")

    # ── Letzte Trades ─────────────────────────────────────────────────────────
    if recent:
        lines.append("")
        lines.append("🕐 *Letzte Trades*")
        for t in recent:
            pl_pct  = float(t.get("pl_pct", 0))
            pl_abs  = float(t.get("pl_abs", 0))
            sign    = "+" if pl_pct >= 0 else ""
            icon    = "✅" if pl_pct >= 0 else "❌"
            side    = "SHORT" if t.get("side") == "short" else "LONG"
            mkt     = t.get("market", "crypto")
            closed  = t.get("closed_at", "")[:16].replace("T", " ")
            if mkt == "crypto":
                eur_s = f" ({sign}{pl_abs * btc_eur:,.2f} €)" if btc_eur else ""
                pl_s  = f"`{sign}{pl_pct:.2f}%`{eur_s}"
            else:
                pl_s  = f"`{sign}{pl_pct:.2f}%` ({sign}${pl_abs:.2f})"
            lines.append(f"  {icon} `{t['symbol']}` {side} {pl_s} | {closed}")

    # ── Statistik (Crypto, nach Gebühren) ─────────────────────────────────────
    total_pl_btc = stats.get("total_pl", 0)
    total_pl_eur = total_pl_btc * btc_eur if btc_eur else None
    eur_total    = f" (~{'+' if total_pl_eur and total_pl_eur >= 0 else ''}{total_pl_eur:,.2f} €)" if total_pl_eur is not None else ""
    lines += [
        "",
        "📊 *Statistik (gesamt)*",
        f"Trades: `{stats.get('total_trades', 0)}` | Win-Rate: `{stats.get('win_rate', 0):.1f}%`",
        f"Brutto P&L: `{'+' if total_pl_btc >= 0 else ''}{total_pl_btc:.6f} BTC`{eur_total}",
    ]
    fs = data.get("fee_stats", {})
    if fs:
        net_btc  = fs.get("net_pl", 0)
        net_eur  = net_btc * btc_eur if btc_eur else None
        n_sign   = "+" if net_btc >= 0 else ""
        n_eur_s  = f" (~{n_sign}{net_eur:,.2f} €)" if net_eur is not None else ""
        payoff   = fs.get("payoff_ratio")
        be_wr    = fs.get("breakeven_wr")
        p_s      = f"`{payoff:.2f}x`" if payoff is not None else "`–`"
        be_s     = f" _(BE: {be_wr:.0f}%)_" if be_wr else ""
        lines += [
            f"Gebühren: `-{fs.get('total_fees', 0):.6f} BTC`",
            f"Netto P&L: `{n_sign}{net_btc:.6f} BTC`{n_eur_s}",
            f"Payoff-Ratio: {p_s}{be_s}",
        ]

    if cb.get("active"):
        lines.append("\n⚡ *Circuit Breaker AKTIV* — Crypto-Entries gesperrt")

    return "\n".join(lines)


# Aliases für Rückwärtskompatibilität
async def fetch_crypto_status() -> str:
    return await fetch_status()


async def fetch_stocks_status() -> str:
    return await fetch_status()


async def trigger_scan(market: str = "all") -> str:
    result = await _post("/scan", params={"market": market})
    if not result:
        return "⚠️ Trade Engine nicht erreichbar."
    return f"🔍 Scan gestartet (`{market}`) — Signale kommen per Push."


async def control_crypto(action: str) -> str:
    """pause | resume | start (= resume alias) | stop (= pause alias)"""
    if action in ("pause", "stop"):
        result = await _post("/crypto/pause")
        if not result:
            return "⚠️ Trade Engine nicht erreichbar."
        icon = "⏸️" if action == "pause" else "🛑"
        label = "pausiert" if action == "pause" else "gestoppt"
        return f"{icon} Crypto-Entries {label} — offene Positionen laufen weiter."
    if action in ("resume", "start"):
        result = await _post("/crypto/resume")
        if not result:
            return "⚠️ Trade Engine nicht erreichbar."
        return "▶️ Crypto-Entries wieder aktiv."
    return f"⚠️ Unbekannte Aktion: `{action}`"
