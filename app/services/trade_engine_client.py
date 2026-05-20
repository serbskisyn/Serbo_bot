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


# Gebühren-Modelle
_KRAKEN_FEE_PER_LEG  = 0.0008      # 0.08 % Maker-Fee bei Kraken (BTC-Pairs)
_ALPACA_SEC_PER_SALE = 0.0000278   # SEC-Fee 0.00278 % auf Verkaufserlös (2026)
_ALPACA_TAF_PER_SHARE = 0.000166   # TAF 0.0166 ¢ pro verkauftem Share (2026)


def _trade_fee(t: dict, market: str) -> float:
    """Round-Trip-Gebühr pro Trade in der jeweiligen Basis-Währung."""
    entry = float(t.get("entry_price", 0))
    exit_ = float(t.get("exit_price", 0))
    qty   = float(t.get("qty", 0))
    if market == "crypto":
        # Beide Legs × Maker-Fee, in BTC
        return (entry + exit_) * qty * _KRAKEN_FEE_PER_LEG
    # Alpaca: kommissionsfrei, nur SEC + TAF auf Verkaufsseite (USD)
    sell_notional = exit_ * qty
    return sell_notional * _ALPACA_SEC_PER_SALE + qty * _ALPACA_TAF_PER_SHARE


def _platform_stats(trades: list, market: str = "crypto") -> dict:
    """Berechnet Statistik-Kennzahlen aus einer Liste abgeschlossener Trades."""
    if not trades:
        return {"trades": 0, "gross_pl": 0.0, "fees": 0.0, "net_pl": 0.0,
                "win_rate": 0.0, "payoff": None, "wins": 0, "losses": 0}
    pl_abs   = [float(t.get("pl_abs", 0)) for t in trades]
    pl_pct   = [float(t.get("pl_pct", 0)) for t in trades]
    fees     = [_trade_fee(t, market) for t in trades]
    win_abs  = [a for a, p in zip(pl_abs, pl_pct) if p > 0]
    loss_abs = [a for a, p in zip(pl_abs, pl_pct) if p <= 0]
    avg_win  = sum(win_abs) / len(win_abs) if win_abs else 0.0
    avg_loss = abs(sum(loss_abs) / len(loss_abs)) if loss_abs else 0.0
    payoff   = avg_win / avg_loss if avg_loss > 0 else None
    return {
        "trades":   len(trades),
        "gross_pl": sum(pl_abs),
        "fees":     sum(fees),
        "net_pl":   sum(pl_abs) - sum(fees),
        "win_rate": len(win_abs) / len(trades) * 100,
        "wins":     len(win_abs),
        "losses":   len(loss_abs),
        "payoff":   payoff,
    }


async def fetch_status() -> str:
    """Kombinierter Status: Kraken Crypto + Alpaca Stocks in einer Ansicht."""
    data, all_trades, recent = await asyncio.gather(
        _get("/status"),
        _get("/trades/recent?limit=1000"),
        _get("/trades/recent?limit=5"),
    )
    if not data:
        return "🔴 *Trade Engine offline* (Port 8081 nicht erreichbar)."

    crypto  = data.get("crypto", {})
    stocks  = data.get("stocks", {})
    cb      = data.get("circuit_breaker", {})
    fs      = data.get("fee_stats", {})
    btc_eur = await _btc_to_eur()
    now     = datetime.now(ET).strftime("%d.%m.%Y %H:%M ET")
    lines   = [f"🤖 *Trading Bot — {now}*"]

    # ── Kraken Crypto ─────────────────────────────────────────────────────────
    lines.append("")
    if crypto.get("enabled"):
        acc     = crypto.get("account", {})
        btc_bal = float(acc.get("balance", 0)) if acc else None
        c_pos   = crypto.get("positions", [])
        eur_s   = f" (~{btc_bal * btc_eur:,.2f} €)" if btc_bal and btc_eur else ""
        bal_s   = f"`{btc_bal:.6f} BTC`{eur_s}" if btc_bal is not None else "–"
        lines.append("🪙 *Kraken Crypto* | 24/7")
        lines.append(f"💰 {bal_s}")
        lines.append(f"📈 Positionen: *{len(c_pos)}*")
        for p in c_pos:
            entry = float(p.get("entry_price", 0))
            s_tag = "🔴 SHORT" if p.get("side") == "short" else "🟢 LONG"
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
        acc    = stocks.get("account", {})
        s_pos  = stocks.get("positions", [])
        equity = float(acc.get("equity", 0))
        cash   = float(acc.get("cash", 0))
        day_pl = equity - float(acc.get("last_equity", equity))
        d_sign = "+" if day_pl >= 0 else ""
        m_st   = "🟢 Offen" if stocks.get("market_open") else "🔴 Geschlossen"
        mode   = "Paper" if acc.get("mode") == "paper" else "Live"
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
            pl_pct = float(t.get("pl_pct", 0))
            pl_abs = float(t.get("pl_abs", 0))
            sign   = "+" if pl_pct >= 0 else ""
            icon   = "✅" if pl_pct >= 0 else "❌"
            side   = "SHORT" if t.get("side") == "short" else "LONG"
            closed = t.get("closed_at", "")[:16].replace("T", " ")
            is_crypto = "/" in t.get("symbol", "")
            if is_crypto:
                eur_s = f" ({sign}{pl_abs * btc_eur:,.2f} €)" if btc_eur else ""
                pl_s  = f"`{sign}{pl_pct:.2f}%`{eur_s}"
            else:
                pl_s = f"`{sign}{pl_pct:.2f}%` ({sign}${pl_abs:.2f})"
            lines.append(f"  {icon} `{t['symbol']}` {side} {pl_s} | {closed}")

    # ── Statistik per Platform ─────────────────────────────────────────────────
    all_t    = all_trades or []
    c_trades = [t for t in all_t if "/" in t.get("symbol", "")]
    s_trades = [t for t in all_t if "/" not in t.get("symbol", "")]
    cs = _platform_stats(c_trades, market="crypto")
    ss = _platform_stats(s_trades, market="stocks")

    lines += ["", "📊 *Statistik*"]

    # Kraken Crypto — P&L aus Summe der pl_abs, Fees aus 0.08 % Maker pro Leg
    lines.append("")
    lines.append("🪙 *Kraken Crypto*")
    if cs["trades"]:
        g_btc   = cs["gross_pl"]
        f_btc   = cs["fees"]
        n_btc   = cs["net_pl"]
        g_sign  = "+" if g_btc >= 0 else ""
        n_sign  = "+" if n_btc >= 0 else ""
        g_eur_s = f" (~{g_sign}{g_btc * btc_eur:,.2f} €)" if btc_eur else ""
        n_eur_s = f" (~{n_sign}{n_btc * btc_eur:,.2f} €)" if btc_eur else ""
        lines.append(f"Trades: `{cs['trades']}` | Win-Rate: `{cs['win_rate']:.1f}%`")
        lines.append(f"Brutto P&L: `{g_sign}{g_btc:.8f} BTC`{g_eur_s}")
        lines.append(f"Gebühren: `-{f_btc:.8f} BTC` _(Maker 0,08 %/Leg)_")
        lines.append(f"Netto P&L: `{n_sign}{n_btc:.8f} BTC`{n_eur_s}")
        payoff_s = f"`{cs['payoff']:.2f}x`" if cs["payoff"] is not None else "`–`"
        lines.append(f"Payoff-Ratio: {payoff_s}")
    else:
        lines.append("_Noch keine abgeschlossenen Trades_")

    # Alpaca Stocks — Gebühren = nur SEC + TAF auf Verkaufsseite (kommissionsfrei)
    lines.append("")
    lines.append("📈 *Alpaca Stocks*")
    if ss["trades"]:
        g_usd  = ss["gross_pl"]
        f_usd  = ss["fees"]
        n_usd  = ss["net_pl"]
        g_sign = "+" if g_usd >= 0 else ""
        n_sign = "+" if n_usd >= 0 else ""
        lines.append(f"Trades: `{ss['trades']}` | Win-Rate: `{ss['win_rate']:.1f}%`")
        lines.append(f"Brutto P&L: `{g_sign}${g_usd:.2f}`")
        lines.append(f"Gebühren: `-${f_usd:.4f}` _(SEC + TAF, kommissionsfrei)_")
        lines.append(f"Netto P&L: `{n_sign}${n_usd:.2f}`")
        payoff_s = f"`{ss['payoff']:.2f}x`" if ss["payoff"] is not None else "`–`"
        lines.append(f"Payoff-Ratio: {payoff_s}")
    else:
        lines.append("_Noch keine abgeschlossenen Trades_")

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
