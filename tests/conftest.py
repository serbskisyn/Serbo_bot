import pytest
from app.security import rate_limiter
from app.bot import memory as mem_module
from app.bot import profile as profile_module


@pytest.fixture(autouse=True)
def reset_rate_limit_store():
    rate_limiter._windows.clear()
    yield
    rate_limiter._windows.clear()


@pytest.fixture(autouse=True)
def reset_memory_store(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_module, "PROFILE_FILE", tmp_path / "profile.yaml")
    monkeypatch.setattr(mem_module, "MEMORY_FILE", tmp_path / "profile.yaml")
    profile_module._store.clear()
    mem_module._store = profile_module._store
    yield
    profile_module._store.clear()