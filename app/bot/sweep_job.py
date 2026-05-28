"""
sweep_job.py — Daily backtest sweep (Trade Engine) → JSONL für das Briefing.

Läuft täglich um SWEEP_HOUR:SWEEP_MINUTE (Europe/Berlin), feuert das
trade_engine-Skript via Subprocess + dessen eigenes venv (saubere Repo-
Trennung), das schreibt eine JSON-Zeile mit den Tages-Ergebnissen
(R, Kelly, beste Trail-Param) nach trade_engine/data/sweep_history.jsonl.

Das morgendliche Briefing liest die letzte Zeile und zeigt einen
Kompakt-Block "📊 Backtest Pulse" — so siehst du den R/Kelly-Verlauf
täglich, ohne extra Push.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram.ext import Application, ContextTypes

from app.config import SWEEP_ENABLED, SWEEP_HOUR, SWEEP_MINUTE

logger = logging.getLogger(__name__)

_BERLIN = ZoneInfo("Europe/Berlin")

_TRADE_ENGINE_DIR = Path("/home/pi/trade_engine")
_PY = _TRADE_ENGINE_DIR / ".venv" / "bin" / "python"
SWEEP_HISTORY_FILE = _TRADE_ENGINE_DIR / "data" / "sweep_history.jsonl"

_TIMEOUT_SEC = 240


async def run_sweep_once() -> bool:
    """Run the sweep subprocess once. Returns True on success."""
    if not _PY.exists():
        logger.warning("sweep_job: trade_engine venv not found at %s", _PY)
        return False
    logger.info("sweep_job: starting daily backtest sweep")
    try:
        proc = await asyncio.create_subprocess_exec(
            str(_PY), "-m", "scripts.backtest_exits", "--out", str(SWEEP_HISTORY_FILE),
            cwd=str(_TRADE_ENGINE_DIR),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("sweep_job: timed out after %ds", _TIMEOUT_SEC)
            return False
    except Exception as exc:
        logger.warning("sweep_job: subprocess failed: %s", exc)
        return False

    if proc.returncode != 0:
        err = (stderr or b"").decode(errors="ignore")[:300]
        logger.warning("sweep_job: exit %s — stderr=%s", proc.returncode, err)
        return False

    logger.info("sweep_job: sweep complete, summary at %s", SWEEP_HISTORY_FILE)
    return True


async def _daily_sweep_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_sweep_once()


def register_sweep_job(application: Application) -> None:
    if not SWEEP_ENABLED:
        logger.info("Daily Backtest Sweep deaktiviert (SWEEP_ENABLED=false)")
        return
    jq = application.job_queue
    if jq is None:
        logger.warning("register_sweep_job: no JobQueue available")
        return
    jq.run_daily(
        callback=_daily_sweep_callback,
        time=time(hour=SWEEP_HOUR, minute=SWEEP_MINUTE, tzinfo=_BERLIN),
        name="daily_backtest_sweep",
    )
    logger.info(
        "Daily Backtest Sweep registriert: %02d:%02d Europe/Berlin",
        SWEEP_HOUR, SWEEP_MINUTE,
    )
