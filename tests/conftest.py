import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests with asyncio only."""

    return "asyncio"
