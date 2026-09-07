import asyncio
import json
from typing import Any

import httpx
import pytest

from mcp_zotero.settings import Settings
from mcp_zotero.zotero_client import ZoteroClient, ZoteroNotFoundError


def settings(**overrides: Any) -> Settings:
    return Settings(
        zotero_group_id=123456,
        zotero_api_key="secret-key",
        zotero_max_retries=overrides.pop("zotero_max_retries", 0),
        **overrides,
    )


def response(request: httpx.Request, payload: Any, **headers: str) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        content=json.dumps(payload),
        headers={"Content-Type": "application/json", **headers},
    )


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def test_search_uses_web_api_v3_and_everything_mode() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return response(request, [], **{"Total-Results": "0"})

    async def scenario() -> None:
        async with ZoteroClient(
            settings(), transport=httpx.MockTransport(handler)
        ) as client:
            page = await client.search_items("climate change")
            assert page.total_results == 0

    run(scenario())

    assert seen_request is not None
    assert seen_request.url.path == "/groups/123456/items"
    assert seen_request.url.params["q"] == "climate change"
    assert seen_request.url.params["qmode"] == "everything"
    assert seen_request.headers["Zotero-API-Version"] == "3"
    assert seen_request.headers["Zotero-API-Key"] == "secret-key"


def test_item_key_requests_are_batched_at_fifty() -> None:
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys = request.url.params["itemKey"].split(",")
        batch_sizes.append(len(keys))
        items = [
            {"key": key, "data": {"key": key, "itemType": "journalArticle"}}
            for key in keys
        ]
        return response(request, items, **{"Total-Results": str(len(items))})

    keys = [f"KEY{i:05d}" for i in range(51)]

    async def scenario() -> list[dict[str, Any]]:
        async with ZoteroClient(
            settings(), transport=httpx.MockTransport(handler)
        ) as client:
            return await client.get_items_by_keys(keys)

    items = run(scenario())

    assert len(items) == 51
    assert batch_sizes == [50, 1]


def test_retries_rate_limited_request() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(429, request=request, headers={"Retry-After": "0"})
        return response(request, {"content": "text"})

    async def scenario() -> dict[str, Any]:
        async with ZoteroClient(
            settings(zotero_max_retries=1),
            transport=httpx.MockTransport(handler),
        ) as client:
            return await client.get_fulltext("ATTACH01")

    assert run(scenario())["content"] == "text"
    assert requests == 2


def test_fulltext_not_found_has_specific_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    async def scenario() -> None:
        async with ZoteroClient(
            settings(), transport=httpx.MockTransport(handler)
        ) as client:
            await client.get_fulltext("ATTACH01")

    with pytest.raises(ZoteroNotFoundError):
        run(scenario())
