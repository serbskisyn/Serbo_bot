import pytest
from app.security.rate_limiter import is_rate_limited
from app import config


def test_first_request_is_allowed():
    limited, retry_after = is_rate_limited(user_id=9001)
    assert limited is False
    assert retry_after == 0


def test_under_limit_stays_allowed(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_REQUESTS", 3)
    for _ in range(2):
        limited, _ = is_rate_limited(user_id=9002)
        assert limited is False


def test_over_limit_is_blocked(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_REQUESTS", 3)
    for _ in range(3):
        is_rate_limited(user_id=9003)
    limited, retry_after = is_rate_limited(user_id=9003)
    assert limited is True
    assert retry_after > 0


def test_different_users_are_independent(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_REQUESTS", 2)
    for _ in range(2):
        is_rate_limited(user_id=9004)
    limited_9004, _ = is_rate_limited(user_id=9004)
    limited_9005, _ = is_rate_limited(user_id=9005)
    assert limited_9004 is True
    assert limited_9005 is False


def test_retry_after_is_positive(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_REQUESTS", 1)
    is_rate_limited(user_id=9006)
    limited, retry_after = is_rate_limited(user_id=9006)
    assert limited is True
    assert retry_after >= 1
