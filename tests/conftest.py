import pytest

@pytest.fixture(autouse=True)
def reset_rate_limit_store():
    yield