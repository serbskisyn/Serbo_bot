import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import (
    OPENROUTER_API_KEY, TAVILY_API_KEY, BRAVE_API_KEY,
    ADMIN_CHAT_ID, NEWS_CACHE_DB_PATH,
)
from app.bot.bot_context import get_bot

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"
_LOG_DIR  = Path(__file__).parent.parent.parent / "logs"


async def _check(name: str, coro) -> tuple[bool, str]:
    try:
        return await asyncio.wait_for(coro, timeout=10.0)
    except asyncio.TimeoutError:
        return False, f"{name}: Timeout"
    except Exception as e:
        return False, f"{name}: {e}"


async def _chk_openrouter() -> tuple[bool, str]:
    if not OPENROUTER_API_KEY:
        return False, "OPENROUTER_API_KEY fehlt"
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        )
        ok = r.status_code == 200
        return ok, "OK" if ok else f"HTTP {r.status_code}"


async def _chk_tavily() -> tuple[bool, str]:
    if not TAVILY_API_KEY:
        return False, "TAVILY_API_KEY fehlt"
    return True, "konfiguriert"


async def _chk_brave() -> tuple[bool, str]:
    if not BRAVE_API_KEY:
        return True, "nicht konfiguriert (optional)"
    return True, "konfiguriert"


async def _chk_disk() -> tuple[bool, str]:
    total, used, free = shutil.disk_usage("/")
    pct = used / total * 100
    free_gb = free / (1024 ** 3)
    ok = pct < 85
    return ok, f"{pct:.1f}% belegt, {free_gb:.1f} GB frei"


async def _chk_news_cache() -> tuple[bool, str]:
    db_path = Path(NEWS_CACHE_DB_PATH)
    if not db_path.exists():
        return False, "news_cache.db nicht gefunden"
    import aiosqlite
    async with aiosqlite.connect(str(db_path)) as db:
        cur = await db.execute("SELECT COUNT(*) FROM news_cache")
        row = await cur.fetchone()
        count = row[0] if row else 0
        cur2 = await db.execute(
            "SELECT MAX(cached_at) FROM cache_meta"
        )
        row2 = await cur2.fetchone()
        last = row2[0] if row2 and row2[0] else "unbekannt"
    return True, f"{count} Artikel, letztes Refresh: {last}"


async def _chk_sqlite_conv() -> tuple[bool, str]:
    db_path = _DATA_DIR / "conversation.db"
    if not db_path.exists():
        return True, "noch nicht angelegt (kein Problem)"
    import aiosqlite
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("SELECT 1")
    return True, "erreichbar"


async def _chk_logdir() -> tuple[bool, str]:
    _LOG_DIR.mkdir(exist_ok=True)
    test = _LOG_DIR / ".health_write_test"
    test.write_text("ok")
    test.unlink()
    return True, str(_LOG_DIR)


CHECKS = [
    ("OpenRouter API",    _chk_openrouter),
    ("Tavily API",        _chk_tavily),
    ("Brave Search",      _chk_brave),
    ("Disk Space",        _chk_disk),
    ("News Cache DB",     _chk_news_cache),
    ("Conversation DB",   _chk_sqlite_conv),
    ("Log-Verzeichnis",   _chk_logdir),
]


async def run_health_check() -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [f"🩺 *Health Check — {now}*\n"]
    all_ok = True

    tasks = [_check(name, fn()) for name, fn in CHECKS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for (name, _), result in zip(CHECKS, results):
        if isinstance(result, Exception):
            ok, msg = False, str(result)
        else:
            ok, msg = result
        icon = "✅" if ok else "❌"
        lines.append(f"{icon} *{name}*: {msg}")
        if not ok:
            all_ok = False

    lines.append(f"\n{'✅ Alles OK' if all_ok else '⚠️ Probleme gefunden'}")
    return "\n".join(lines)


async def send_daily_health_check(context) -> None:
    if not ADMIN_CHAT_ID:
        logger.warning("Health Check: ADMIN_CHAT_ID nicht gesetzt")
        return
    try:
        report = await run_health_check()
        bot = get_bot()
        if bot:
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=report,
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.error("Health Check senden fehlgeschlagen: %s", e)
