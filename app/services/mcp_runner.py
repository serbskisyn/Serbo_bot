"""
mcp_runner.py — Semaphore-gated wrapper for Claude-subprocess MCP calls.

Pi 4 hits memory pressure when two Claude subprocesses run concurrently
(each is ~700 MB resident + swap is only 199 MB → OOM-killer risk).
All MCP-via-Claude callers (Pepper, Granola, …) should route through
this module so only one subprocess runs at a time.

Use:
    from app.services.mcp_runner import run_mcp_subprocess

    raw = await run_mcp_subprocess(prompt, timeout=180, label="pepper")

The semaphore is module-global, so concurrent /leads + /briefing jobs
serialize automatically.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.services.claude_runner import run_claude_agent

logger = logging.getLogger(__name__)

_MCP_SEMAPHORE = asyncio.Semaphore(1)


async def run_mcp_subprocess(prompt: str, timeout: int = 180, label: str = "mcp") -> str:
    """Run a Claude subprocess with the global MCP gate.

    Logs queue wait + run duration so we can spot contention later.
    Returns raw stdout — caller is responsible for JSON-parsing/validation.
    """
    queue_start = time.monotonic()
    async with _MCP_SEMAPHORE:
        queued_for = time.monotonic() - queue_start
        if queued_for > 1.0:
            logger.info("mcp_runner[%s]: queued for %.1fs", label, queued_for)
        run_start = time.monotonic()
        try:
            raw = await run_claude_agent(prompt, timeout=timeout)
        finally:
            ran_for = time.monotonic() - run_start
            logger.info("mcp_runner[%s]: ran for %.1fs", label, ran_for)
    return raw
