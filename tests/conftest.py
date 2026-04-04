import pytest

@pytest.fixture(autouse=True)
def reset_rate_limit_store():
    from app.security import injection_guard
    injection_guard._rate_limit_store.clear()
    yield
    injection_guard._rate_limit_store.clear()
