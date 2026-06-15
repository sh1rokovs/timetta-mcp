import pytest

from timetta_mcp import server


@pytest.fixture(autouse=True)
def reset_token_provider_singleton():
    server._reset_token_provider()
    yield
    server._reset_token_provider()
