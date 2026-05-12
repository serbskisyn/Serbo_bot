import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CLAUDE_BIN = "/home/pi/.local/bin/claude"
WORKDIR = Path(__file__).parent.parent.parent  # /home/pi/Serbo_bot


async def run_claude(prompt: str, timeout: int = 120) -> str:
    logger.info("Claude CLI gestartet | prompt=%r", prompt[:80])
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "--print", "--output-format", "text", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKDIR),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        logger.warning("Claude CLI Timeout nach %ds", timeout)
        return f"⏳ Claude hat nach {timeout}s nicht geantwortet."
    except Exception as e:
        logger.exception("Claude CLI Fehler")
        return f"❌ Fehler beim Starten von Claude: {e}"

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[:500]
        logger.error("Claude CLI exit %d: %s", proc.returncode, err)
        return f"❌ Claude Fehler (exit {proc.returncode}):\n{err}"

    result = stdout.decode(errors="replace").strip()
    logger.info("Claude CLI fertig | %d Zeichen", len(result))
    return result or "_(Keine Ausgabe)_"


async def run_claude_agent_continue(prompt: str, timeout: int = 300) -> str:
    """Setzt die letzte Claude-Agent-Session fort (--continue)."""
    logger.info("Claude Agent (--continue) | prompt=%r", prompt[:80])
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
            "--continue", "--output-format", "text", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKDIR),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        logger.warning("Claude Agent (continue) Timeout nach %ds", timeout)
        return f"⏳ Claude Agent hat nach {timeout}s nicht geantwortet."
    except Exception as e:
        logger.exception("Claude Agent (continue) Fehler")
        return f"❌ Fehler: {e}"

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[:500]
        logger.error("Claude Agent (continue) exit %d: %s", proc.returncode, err)
        return f"❌ Claude Agent Fehler (exit {proc.returncode}):\n{err}"

    result = stdout.decode(errors="replace").strip()
    logger.info("Claude Agent (continue) fertig | %d Zeichen", len(result))
    return result or "_(Keine Ausgabe)_"


async def run_claude_agent(prompt: str, timeout: int = 300) -> str:
    """Führt Claude mit vollem Tool-Zugriff aus (Dateien lesen/schreiben, Git, Bash)."""
    logger.info("Claude Agent gestartet | prompt=%r", prompt[:80])
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
            "--output-format", "text", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKDIR),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        logger.warning("Claude Agent Timeout nach %ds", timeout)
        return f"⏳ Claude Agent hat nach {timeout}s nicht geantwortet."
    except Exception as e:
        logger.exception("Claude Agent Fehler")
        return f"❌ Fehler beim Starten von Claude Agent: {e}"

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[:500]
        logger.error("Claude Agent exit %d: %s", proc.returncode, err)
        return f"❌ Claude Agent Fehler (exit {proc.returncode}):\n{err}"

    result = stdout.decode(errors="replace").strip()
    logger.info("Claude Agent fertig | %d Zeichen", len(result))
    return result or "_(Keine Ausgabe)_"
