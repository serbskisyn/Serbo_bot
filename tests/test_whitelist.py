import pytest
from app.bot.whitelist import is_allowed
from app import config


def test_allowed_user(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", {123, 456})
    assert is_allowed(123) is True


def test_unknown_user_is_blocked(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", {123})
    assert is_allowed(999) is False


def test_empty_whitelist_blocks_everyone(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", set())
    assert is_allowed(1) is False


def test_negative_user_id_works(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", {-1})
    assert is_allowed(-1) is True


def test_multiple_users_all_allowed(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", {1, 2, 3})
    for uid in [1, 2, 3]:
        assert is_allowed(uid) is True
