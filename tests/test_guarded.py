"""Tests für den @guarded Decorator (Dedup + Whitelist + Rate-Limit)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app import config
from app.bot import whitelist
from app.bot.whitelist import guarded
from app.security import rate_limiter


@pytest.fixture
def reset_dedup():
    whitelist._seen_update_ids.clear()
    yield
    whitelist._seen_update_ids.clear()


def _make_update(user_id: int = 123, update_id: int = 1):
    update = MagicMock()
    update.update_id = update_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


@pytest.mark.anyio
async def test_guarded_allows_whitelisted_user(monkeypatch, reset_dedup):
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", {123})
    rate_limiter._windows.clear()

    inner = AsyncMock(return_value="ok")
    wrapped = guarded(inner)

    update = _make_update(user_id=123, update_id=1)
    result = await wrapped(update, MagicMock())

    assert result == "ok"
    inner.assert_awaited_once()


@pytest.mark.anyio
async def test_guarded_blocks_non_whitelisted(monkeypatch, reset_dedup):
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", {123})

    inner = AsyncMock()
    wrapped = guarded(inner)

    update = _make_update(user_id=999, update_id=2)
    result = await wrapped(update, MagicMock())

    assert result is None
    inner.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()
    args, _ = update.message.reply_text.await_args
    assert "Kein Zugriff" in args[0]


@pytest.mark.anyio
async def test_guarded_dedups_repeated_update_id(monkeypatch, reset_dedup):
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", {123})
    rate_limiter._windows.clear()

    inner = AsyncMock(return_value="ok")
    wrapped = guarded(inner)

    update1 = _make_update(user_id=123, update_id=42)
    update2 = _make_update(user_id=123, update_id=42)

    await wrapped(update1, MagicMock())
    await wrapped(update2, MagicMock())

    assert inner.await_count == 1


@pytest.mark.anyio
async def test_guarded_rate_limit_blocks(monkeypatch, reset_dedup):
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", {123})
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_REQUESTS", 2)
    monkeypatch.setattr(config, "RATE_LIMIT_WINDOW_SECONDS", 60)
    rate_limiter._windows.clear()

    inner = AsyncMock(return_value="ok")
    wrapped = guarded(inner)

    # Erste zwei Calls gehen durch, dritter wird geblockt.
    for i in range(3):
        update = _make_update(user_id=123, update_id=100 + i)
        await wrapped(update, MagicMock())

    assert inner.await_count == 2
