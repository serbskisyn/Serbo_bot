"""
trade_recap.py — 7-day rolling R/Kelly + live-trade pulse for the daily 08:15 push.

Combines two data sources side-by-side per day:
  • Backtest sweep   (trade_engine/data/sweep_history.jsonl) — best R/Kelly the
    sweep found that day (target R-evolution signal).
  • Live realised trades (trade_engine/data/trades.db, trade_log table) —
    actual count, win-rate, realised net P&L closed that day.

Output is a compact list-per-day (Telegram-friendly, no wide tables) so the
user can scan the R-trend at a glance. Missing data on either side shows
as "—".
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

SWEEP_HISTORY_FILE = Path("/home/pi/trade_engine/data/sweep_history.jsonl")
TRADES_DB = Path("/home/pi/trade_engine/data/trades.db")


# ── Sweep history (one JSON object per day) ──────────────────────────────────


def _read_sweep_by_date(days: int) -> dict[str, dict]:
    """Map "YYYY-MM-DD" → latest sweep entry of that day."""
    if not SWEEP_HISTORY_FILE.exists():
        return {}
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    out: dict[str, dict] = {}
    try:
        with SWEEP_HISTORY_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                d = entry.get("date", "")
                if d >= cutoff:
                    out[d] = entry  # last write wins per day
    except Exception as exc:
        logger.debug("trade_recap: sweep read skipped: %s", exc)
    return out


# ── Realised trades from trade_log ───────────────────────────────────────────


def _read_trades_by_date(days: int) -> dict[str, list[dict]]:
    """Map "YYYY-MM-DD" → list of trade rows closed that day."""
    if not TRADES_DB.exists():
        return {}
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    out: dict[str, list[dict]] = {}
    try:
        con = sqlite3.connect(str(TRADES_DB))
        con.row_factory = sqlite3.Row
        cur = con.execute(
            """SELECT closed_at, market, symbol, side, entry_price, exit_price,
                      pl_pct, pl_abs, reason
               FROM trade_log
               WHERE closed_at >= ?
               ORDER BY closed_at""",
            (cutoff,),
        )
        for r in cur:
            d = r["closed_at"][:10]
            out.setdefault(d, []).append({
                "market": r["market"], "symbol": r["symbol"], "side": r["side"],
                "pl_pct": r["pl_pct"] or 0.0, "reason": r["reason"] or "",
            })
        con.close()
    except Exception as exc:
        logger.debug("trade_recap: trade_log read skipped: %s", exc)
    return out


# ── KPI helpers ──────────────────────────────────────────────────────────────


def _live_kpis(trades: list[dict]) -> dict | None:
    if not trades:
        return None
    pls = [t["pl_pct"] / 100.0 for t in trades]  # convert % to fraction
    wins = [p for p in pls if p > 0]
    losses = [p for p in pls if p <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    r = (avg_win / abs(avg_loss)) if avg_loss != 0 else None
    return {
        "n": len(trades),
        "wr": len(wins) / len(trades),
        "net": sum(pls),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "r": r,
    }


# ── Output composition ───────────────────────────────────────────────────────


def _format_day(d: date, sweep: dict | None, kpis: dict | None) -> str:
    """One line per day, Telegram-friendly."""
    day_str = d.strftime("%a %d.%m.").replace("Mon", "Mo").replace("Tue", "Di").replace(
        "Wed", "Mi").replace("Thu", "Do").replace("Fri", "Fr").replace("Sat", "Sa").replace("Sun", "So")

    # Backtest snapshot
    if sweep:
        bk = sweep.get("best_by_kelly") or sweep.get("best_by_expectancy") or {}
        bt_r = bk.get("r")
        bt_k = bk.get("kelly")
        bt_part = f"BT R={bt_r:.2f} K={bt_k*100:+.1f}%" if bt_r is not None else "BT —"
    else:
        bt_part = "BT —"

    # Live realised
    if kpis:
        n, wr, net = kpis["n"], kpis["wr"], kpis["net"]
        r_str = f" R={kpis['r']:.2f}" if kpis.get("r") is not None else ""
        live_part = f"Live {n}T WR={wr*100:.0f}% Net={net*100:+.2f}%{r_str}"
    else:
        live_part = "Live —"

    return f"`{day_str}`  {bt_part}  ·  {live_part}"


def build_recap(days: int = 7) -> str:
    """Markdown block summarising the last `days` days of R/Kelly + live trades."""
    sweeps = _read_sweep_by_date(days)
    live = _read_trades_by_date(days)
    today = date.today()

    lines = [f"📊 *{days}-Tage Trading-Pulse* (Backtest · Live)"]
    any_data = False
    for offset in range(days - 1, -1, -1):
        d = today - timedelta(days=offset)
        key = d.isoformat()
        sweep = sweeps.get(key)
        kpis = _live_kpis(live.get(key) or [])
        if sweep or kpis:
            any_data = True
        lines.append(_format_day(d, sweep, kpis))

    # Cumulative live KPIs across the window
    all_trades = [t for ts in live.values() for t in ts]
    cum = _live_kpis(all_trades)
    if cum:
        lines.append("")
        r_str = f" · R={cum['r']:.2f}" if cum.get("r") is not None else ""
        lines.append(
            f"*Σ Live ({days}d):* {cum['n']} Trades · WR={cum['wr']*100:.0f}% · "
            f"Net={cum['net']*100:+.2f}%{r_str}"
        )
    if not any_data:
        lines.append("_Keine Daten verfügbar (Sweep + Trade-Log leer)._")
    return "\n".join(lines)
