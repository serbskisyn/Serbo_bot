"""
papertrade_status.py — /papertrade Status-Report für den Dry-Run-Modus.

Liest Phantom-Positionen und simulierte Trade-History direkt aus der
trade_engine-DB (mode='dry_run') und kombiniert dies mit dem API-Status.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.trade_engine_client import _get, _btc_to_eur, _trade_fee, KRAKEN_FEE_MAKER

logger = logging.getLogger(__name__)

TRADES_DB = Path("/home/pi/trade_engine/data/trades.db")
_BERLIN = ZoneInfo("Europe/Berlin")


def _read_phantom_positions() -> list[dict]:
    if not TRADES_DB.exists():
        return []
    try:
        con = sqlite3.connect(str(TRADES_DB))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM positions WHERE mode='dry_run' ORDER BY opened_at"
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("papertrade: positions read failed: %s", exc)
        return []


def _read_dry_run_trades() -> list[dict]:
    if not TRADES_DB.exists():
        return []
    try:
        con = sqlite3.connect(str(TRADES_DB))
        con.row_factory = sqlite3.Row
        col_set = {r[1] for r in con.execute("PRAGMA table_info(trade_log)").fetchall()}
        mode_col = "mode" if "mode" in col_set else "'dry_run' AS mode"
        rows = con.execute(
            f"""SELECT market, symbol, side, entry_price, exit_price,
                       pl_pct, pl_abs, reason, closed_at, {mode_col}
               FROM trade_log WHERE mode='dry_run'
               ORDER BY closed_at"""
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("papertrade: trade_log read failed: %s", exc)
        return []


def _stats(trades: list[dict]) -> dict:
    if not trades:
        return {}
    pl_abs  = [float(t.get("pl_abs", 0)) for t in trades]
    pl_pct  = [float(t.get("pl_pct", 0)) for t in trades]
    fees    = [_trade_fee(t, t.get("market", "crypto")) for t in trades]
    wins    = [a for a, p in zip(pl_abs, pl_pct) if p > 0]
    losses  = [a for a, p in zip(pl_abs, pl_pct) if p <= 0]
    avg_win  = sum(wins)  / len(wins)  if wins   else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    return {
        "n":        len(trades),
        "wins":     len(wins),
        "losses":   len(losses),
        "win_rate": len(wins) / len(trades) * 100,
        "gross":    sum(pl_abs),
        "fees":     sum(fees),
        "net":      sum(pl_abs) - sum(fees),
        "payoff":   avg_win / avg_loss if avg_loss > 0 else None,
        "best":     max(pl_pct),
        "worst":    min(pl_pct),
    }


def _fmt_dt(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso).astimezone(_BERLIN)
        return dt.strftime("%d.%m. %H:%M")
    except Exception:
        return iso[:16]


async def build_papertrade_status() -> str:
    status_data, btc_eur = None, 0.0
    try:
        status_data, btc_eur = await __import__("asyncio").gather(
            _get("/status"), _btc_to_eur()
        )
    except Exception:
        pass

    dry_run_active = (status_data or {}).get("dry_run", False)
    mode_icon = "✅ DRY-RUN aktiv" if dry_run_active else "⚠️ DRY-RUN inaktiv (Live!)"

    positions = _read_phantom_positions()
    trades    = _read_dry_run_trades()
    stats     = _stats(trades)
    now       = datetime.now(_BERLIN).strftime("%d.%m.%Y %H:%M")

    lines = [f"🧪 *Paper Trading — {now}*", f"Modus: {mode_icon}", ""]

    # ── Offene Phantom-Positionen ──────────────────────────────────────────────
    lines.append(f"📋 *Offene Phantom-Positionen: {len(positions)}*")
    if positions:
        for p in positions:
            entry  = float(p.get("entry_price", 0))
            s_tag  = "🔴 SHORT" if p.get("side") == "short" else "🟢 LONG"
            trail  = "✅" if p.get("trailing_active") else "⏳"
            opened = _fmt_dt(p.get("opened_at", ""))
            lines.append(
                f"  {s_tag} `{p['symbol']}` | Entry: `{entry:.8f}`"
                f" | {p.get('candles_held', 0)}C | Trail: {trail} | seit {opened}"
            )
    else:
        lines.append("  _Keine offenen Positionen_")

    lines.append("")

    # ── Statistik ──────────────────────────────────────────────────────────────
    lines.append("📊 *Statistik (alle Sim-Trades)*")
    if stats:
        g = stats["gross"]
        f = stats["fees"]
        n = stats["net"]
        g_sign = "+" if g >= 0 else ""
        n_sign = "+" if n >= 0 else ""
        g_eur  = f" (~{g_sign}{g * btc_eur:,.2f} €)" if btc_eur else ""
        n_eur  = f" (~{n_sign}{n * btc_eur:,.2f} €)" if btc_eur else ""
        payoff = f"`{stats['payoff']:.2f}x`" if stats.get("payoff") is not None else "`–`"
        lines.append(
            f"Trades: `{stats['n']}` | WR: `{stats['win_rate']:.1f}%`"
            f" | Wins: `{stats['wins']}` Losses: `{stats['losses']}`"
        )
        lines.append(f"Brutto P&L: `{g_sign}{g:.8f} BTC`{g_eur}")
        lines.append(f"Fees: `-{f:.8f} BTC` _(Maker {KRAKEN_FEE_MAKER*100:.2f}%/Leg)_")
        lines.append(f"Netto P&L: `{n_sign}{n:.8f} BTC`{n_eur}")
        lines.append(f"Payoff-Ratio: {payoff}")
        lines.append(
            f"Bester Trade: `{'+' if stats['best'] >= 0 else ''}{stats['best']:.2f}%` | "
            f"Schlechtester: `{stats['worst']:+.2f}%`"
        )
    else:
        lines.append(
            "_Noch keine abgeschlossenen Sim-Trades._\n"
            "_Bot scannt alle 5 Min — erster Buy-Signal kommt per Push._"
        )

    # ── Letzte Trades ──────────────────────────────────────────────────────────
    if trades:
        lines.append("")
        recent = trades[-5:][::-1]
        lines.append(f"🕐 *Letzte {len(recent)} Sim-Trades*")
        for t in recent:
            pl_pct = float(t.get("pl_pct", 0))
            pl_abs = float(t.get("pl_abs", 0))
            icon   = "✅" if pl_pct >= 0 else "❌"
            sign   = "+" if pl_pct >= 0 else ""
            side   = "SHORT" if t.get("side") == "short" else "LONG"
            closed = _fmt_dt(t.get("closed_at", ""))
            eur_s  = f" (~{sign}{pl_abs * btc_eur:,.2f} €)" if btc_eur else ""
            lines.append(
                f"  {icon} `{t['symbol']}` {side} `{sign}{pl_pct:.2f}%`{eur_s}"
                f" | {t.get('reason', '')[:30]} | {closed}"
            )

    return "\n".join(lines)
