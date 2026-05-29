"""Tests for the 7-day R/Kelly + live-trade recap."""
import json
import sqlite3
from datetime import date, timedelta

import pytest

from app.services import trade_recap


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_recap, "SWEEP_HISTORY_FILE", tmp_path / "sweep_history.jsonl")
    monkeypatch.setattr(trade_recap, "TRADES_DB", tmp_path / "trades.db")


# ── _live_kpis ───────────────────────────────────────────────────────────────


def test_live_kpis_empty():
    assert trade_recap._live_kpis([]) is None


def test_live_kpis_wins_and_losses():
    trades = [
        {"pl_pct": 2.0, "reason": "trail"},
        {"pl_pct": -1.0, "reason": "stop"},
        {"pl_pct": 3.0, "reason": "trail"},
        {"pl_pct": -1.5, "reason": "stop"},
    ]
    kpis = trade_recap._live_kpis(trades)
    assert kpis["n"] == 4
    assert kpis["wr"] == 0.5
    assert abs(kpis["net"] - 0.025) < 1e-9  # 2 + 3 - 1 - 1.5 = 2.5%
    # R = avg_win / |avg_loss| = 2.5 / 1.25 = 2.0
    assert abs(kpis["r"] - 2.0) < 1e-9


def test_live_kpis_all_losses_no_R():
    trades = [{"pl_pct": -1.0}, {"pl_pct": -2.0}]
    kpis = trade_recap._live_kpis(trades)
    assert kpis["wr"] == 0.0
    # avg_win=0 → R = 0 / 1.5 = 0
    assert kpis["r"] == 0.0


# ── Sweep reading ────────────────────────────────────────────────────────────


def test_read_sweep_by_date(monkeypatch):
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    old = (today - timedelta(days=30)).isoformat()  # outside window
    path = trade_recap.SWEEP_HISTORY_FILE
    path.write_text(
        json.dumps({"date": old, "best_by_kelly": {"r": 0.5, "kelly": -0.10}}) + "\n" +
        json.dumps({"date": yesterday, "best_by_kelly": {"r": 1.5, "kelly": 0.05}}) + "\n",
        encoding="utf-8",
    )
    sweeps = trade_recap._read_sweep_by_date(days=7)
    assert yesterday in sweeps
    assert old not in sweeps
    assert sweeps[yesterday]["best_by_kelly"]["r"] == 1.5


# ── Trade-log reading ────────────────────────────────────────────────────────


def _seed_trades(path, rows: list[tuple]):
    con = sqlite3.connect(str(path))
    con.execute("""CREATE TABLE trade_log (
        closed_at TEXT, market TEXT, symbol TEXT, side TEXT,
        entry_price REAL, exit_price REAL, pl_pct REAL, pl_abs REAL, reason TEXT
    )""")
    con.executemany("INSERT INTO trade_log VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_read_trades_by_date(monkeypatch):
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat() + "T10:00:00"
    old = (today - timedelta(days=30)).isoformat() + "T12:00:00"
    _seed_trades(trade_recap.TRADES_DB, [
        (yesterday, "crypto", "ETH/BTC", "long", 1.0, 1.02, 2.0, 0.02, "trail"),
        (old, "crypto", "DOT/BTC", "long", 1.0, 0.98, -2.0, -0.02, "stop"),
    ])
    out = trade_recap._read_trades_by_date(days=7)
    yest_key = (today - timedelta(days=1)).isoformat()
    old_key = (today - timedelta(days=30)).isoformat()
    assert yest_key in out and old_key not in out
    assert out[yest_key][0]["symbol"] == "ETH/BTC"


# ── build_recap end-to-end ───────────────────────────────────────────────────


def test_build_recap_empty_state():
    out = trade_recap.build_recap(days=7)
    assert "Trading-Pulse" in out
    assert "Keine Daten verfügbar" in out


def test_build_recap_with_data():
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()

    trade_recap.SWEEP_HISTORY_FILE.write_text(
        json.dumps({"date": yesterday,
                    "best_by_kelly": {"r": 1.25, "kelly": 0.08, "win_rate": 0.55}}) + "\n",
        encoding="utf-8",
    )
    _seed_trades(trade_recap.TRADES_DB, [
        (yesterday + "T10:00:00", "crypto", "ETH/BTC", "long", 1.0, 1.025, 2.5, 0.025, "trail"),
        (yesterday + "T14:00:00", "crypto", "DOT/BTC", "long", 1.0, 0.985, -1.5, -0.015, "stop"),
    ])

    out = trade_recap.build_recap(days=7)
    assert "BT R=1.25" in out
    assert "K=+8.0%" in out
    assert "Live 2T" in out  # 2 trades yesterday
    assert "WR=50%" in out
    # Cumulative row
    assert "Σ Live" in out
    assert "2 Trades" in out
