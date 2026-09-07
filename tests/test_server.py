from unittest.mock import Mock

import pytest

from mcp_zotero.server import main, mcp
from mcp_zotero.settings import TransportSettings


@pytest.fixture
def clean_transport_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)


def test_transport_settings_default_to_streamable_http(clean_transport_env) -> None:
    assert TransportSettings().mcp_transport == "streamable-http"


def test_transport_settings_accept_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    assert TransportSettings().mcp_transport == "stdio"


def test_main_runs_configured_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    run = Mock()
    monkeypatch.setattr(mcp, "run", run)

    main()

    run.assert_called_once_with(transport="stdio")
