import pytest
from app.security import rate_limiter
from app.bot import memory as mem_module


@pytest.fixture(autouse=True)
def reset_rate_limit_store():
    rate_limiter._windows.clear()
    yield
    rate_limiter._windows.clear()


@pytest.fixture(autouse=True)
def reset_memory_store():
    mem_module._store.clear()
    yield
    mem_module._store.clear()