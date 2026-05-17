import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)

CLAUDE_BIN = "/home/pi/.local/bin/claude"
WORKDIR = Path(__file__).parent.parent.parent  # /home/pi/Serbo_bot
AUDIT_LOG = WORKDIR / "logs" / "claudex_audit.log"

# Dedizierter Audit-Logger: rotierend, thread-/process-safe, ohne Propagation.
_audit_logger = logging.getLogger("serbo_bot.audit")
_audit_logger.setLevel(logging.INFO)
_audit_logger.propagate = False
if not _audit_logger.handlers:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(
        AUDIT_LOG, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    _handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    _audit_logger.addHandler(_handler)


def _audit(tag: str, prompt: str, stdout: bytes, stderr: bytes,
           exit_code: int | None) -> None:
    """Schreibt Claudex-Aufruf an logs/claudex_audit.log (rotiert bei 10 MB, 5 Backups)."""
    try:
        parts = [f"=== {tag} | exit={exit_code} ===",
                 f"PROMPT: {prompt[:4000]}"]
        if stdout:
            parts.append(f"STDOUT:\n{stdout.decode(errors='replace')[:4000]}")
        if stderr:
            parts.append(f"STDERR:\n{stderr.decode(errors='replace')[:4000]}")
        _audit_logger.info("\n".join(parts))
    except Exception as e:
        logger.warning("Audit-Log fehlgeschlagen: %s", e)


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
    stdout, stderr = b"", b""
    exit_code = None
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
            "--continue", "--output-format", "text", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKDIR),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        exit_code = proc.returncode
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        logger.warning("Claude Agent (continue) Timeout nach %ds", timeout)
        _audit("claudex_continue_TIMEOUT", prompt, stdout, stderr, None)
        return f"⏳ Claude Agent hat nach {timeout}s nicht geantwortet."
    except Exception as e:
        logger.exception("Claude Agent (continue) Fehler")
        _audit("claudex_continue_ERROR", prompt, stdout, stderr, None)
        return f"❌ Fehler: {e}"

    _audit("claudex_continue", prompt, stdout, stderr, exit_code)

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
    stdout, stderr = b"", b""
    exit_code = None
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
            "--output-format", "text", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKDIR),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        exit_code = proc.returncode
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        logger.warning("Claude Agent Timeout nach %ds", timeout)
        _audit("claudex_agent_TIMEOUT", prompt, stdout, stderr, None)
        return f"⏳ Claude Agent hat nach {timeout}s nicht geantwortet."
    except Exception as e:
        logger.exception("Claude Agent Fehler")
        _audit("claudex_agent_ERROR", prompt, stdout, stderr, None)
        return f"❌ Fehler beim Starten von Claude Agent: {e}"

    _audit("claudex_agent", prompt, stdout, stderr, exit_code)

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[:500]
        logger.error("Claude Agent exit %d: %s", proc.returncode, err)
        return f"❌ Claude Agent Fehler (exit {proc.returncode}):\n{err}"

    result = stdout.decode(errors="replace").strip()
    logger.info("Claude Agent fertig | %d Zeichen", len(result))
    return result or "_(Keine Ausgabe)_"
