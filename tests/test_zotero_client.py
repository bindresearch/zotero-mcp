import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from mcp_zotero.settings import Settings
from mcp_zotero.zotero_client import ZoteroClient, ZoteroNotFoundError

pytestmark = pytest.mark.anyio


@pytest.fixture
def zotero_settings() -> Settings:
    return Settings(
        zotero_group_id=123456,
        zotero_api_key="secret-key",
        zotero_max_retries=0,
    )


@pytest.fixture
def make_response() -> Callable[..., httpx.Response]:
    def factory(
        request: httpx.Request,
        payload: Any,
        **headers: str,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=json.dumps(payload),
            headers={"Content-Type": "application/json", **headers},
        )

    return factory


@pytest.fixture
def make_client(
    zotero_settings: Settings,
) -> Callable[..., ZoteroClient]:
    def factory(
        handler: Callable[[httpx.Request], httpx.Response],
        **settings_overrides: Any,
    ) -> ZoteroClient:
        settings = zotero_settings.model_copy(update=settings_overrides)
        return ZoteroClient(settings, transport=httpx.MockTransport(handler))

    return factory


async def test_search_uses_web_api_v3_and_everything_mode(
    make_client: Callable[..., ZoteroClient],
    make_response: Callable[..., httpx.Response],
) -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return make_response(request, [], **{"Total-Results": "0"})

    async with make_client(handler) as client:
        page = await client.search_items("climate change")

    assert page.total_results == 0
    assert seen_request is not None
    assert seen_request.url.path == "/groups/123456/items"
    assert seen_request.url.params["q"] == "climate change"
    assert seen_request.url.params["qmode"] == "everything"
    assert seen_request.headers["Zotero-API-Version"] == "3"
    assert seen_request.headers["Zotero-API-Key"] == "secret-key"


async def test_item_key_requests_are_batched_at_fifty(
    make_client: Callable[..., ZoteroClient],
    make_response: Callable[..., httpx.Response],
) -> None:
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys = request.url.params["itemKey"].split(",")
        batch_sizes.append(len(keys))
        items = [
            {"key": key, "data": {"key": key, "itemType": "journalArticle"}}
            for key in keys
        ]
        return make_response(
            request,
            items,
            **{"Total-Results": str(len(items))},
        )

    keys = [f"KEY{i:05d}" for i in range(51)]

    async with make_client(handler) as client:
        items = await client.get_items_by_keys(keys)

    assert len(items) == 51
    assert batch_sizes == [50, 1]


async def test_retries_rate_limited_request(
    make_client: Callable[..., ZoteroClient],
    make_response: Callable[..., httpx.Response],
) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(429, request=request, headers={"Retry-After": "0"})
        return make_response(request, {"content": "text"})

    async with make_client(handler, zotero_max_retries=1) as client:
        fulltext = await client.get_fulltext("ATTACH01")

    assert fulltext["content"] == "text"
    assert requests == 2


async def test_fulltext_not_found_has_specific_error(
    make_client: Callable[..., ZoteroClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    async with make_client(handler) as client:
        with pytest.raises(ZoteroNotFoundError):
            await client.get_fulltext("ATTACH01")
