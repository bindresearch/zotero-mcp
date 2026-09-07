"""Small asynchronous client for the Zotero Web API v3."""

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Any, cast

import httpx

from .settings import Settings


class ZoteroError(RuntimeError):
    """Base error for a Zotero API request."""


class ZoteroAuthenticationError(ZoteroError):
    """The Zotero API rejected the configured credentials."""


class ZoteroNotFoundError(ZoteroError):
    """The requested Zotero object or full-text content was not found."""


class ZoteroRateLimitError(ZoteroError):
    """The Zotero API continued to rate-limit requests after retries."""


@dataclass(frozen=True, slots=True)
class ZoteroPage:
    """One page from a Zotero multi-object response."""

    items: list[dict[str, Any]]
    total_results: int


class ZoteroClient:
    """Read-only Zotero client with bounded retries and backoff handling."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.group_id = int(settings.zotero_group_id)
        self.max_retries = settings.zotero_max_retries
        self._backoff_until = 0.0
        self._backoff_lock = asyncio.Lock()
        self._request_slots = asyncio.Semaphore(4)
        self._client = httpx.AsyncClient(
            base_url=str(settings.zotero_base_url).rstrip("/") + "/",
            headers={
                "Accept": "application/json",
                "User-Agent": "mcp-zotero/0.1.0",
                "Zotero-API-Key": settings.zotero_api_key.get_secret_value(),
                "Zotero-API-Version": "3",
            },
            timeout=httpx.Timeout(settings.zotero_timeout_seconds),
            follow_redirects=False,
            transport=transport,
        )

    async def __aenter__(self) -> "ZoteroClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""

        await self._client.aclose()

    @property
    def _items_path(self) -> str:
        return f"groups/{self.group_id}/items"

    async def _wait_for_backoff(self) -> None:
        while True:
            async with self._backoff_lock:
                delay = self._backoff_until - monotonic()
            if delay <= 0:
                return
            await asyncio.sleep(delay)

    async def _set_backoff(self, seconds: float) -> None:
        if seconds <= 0:
            return
        async with self._backoff_lock:
            self._backoff_until = max(self._backoff_until, monotonic() + seconds)

    @staticmethod
    def _header_seconds(response: httpx.Response, name: str) -> float | None:
        raw = response.headers.get(name)
        if raw is None:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            return None

    async def _request(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        attempts = self.max_retries + 1
        last_transport_error: httpx.HTTPError | None = None

        async with self._request_slots:
            for attempt in range(attempts):
                await self._wait_for_backoff()
                try:
                    response = await self._client.get(path, params=params)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_transport_error = exc
                    if attempt + 1 >= attempts:
                        break
                    await asyncio.sleep(min(2**attempt, 8))
                    continue

                backoff = self._header_seconds(response, "Backoff")
                if backoff is not None:
                    await self._set_backoff(backoff)

                if (
                    response.status_code in {429, 503}
                    or 500 <= response.status_code < 600
                ):
                    if attempt + 1 < attempts:
                        retry_after = self._header_seconds(response, "Retry-After")
                        await asyncio.sleep(
                            retry_after
                            if retry_after is not None
                            else min(2**attempt, 8)
                        )
                        continue
                    if response.status_code == 429:
                        raise ZoteroRateLimitError(
                            "Zotero rate-limited the request after all retries. Try again later."
                        )

                if response.status_code in {401, 403}:
                    raise ZoteroAuthenticationError(
                        "Zotero rejected the API key or denied access to the group library."
                    )
                if response.status_code == 404:
                    raise ZoteroNotFoundError(
                        "The requested Zotero item or full text was not found."
                    )
                if response.is_error:
                    raise ZoteroError(
                        f"Zotero API request failed with HTTP {response.status_code}."
                    )
                return response

        if last_transport_error is not None:
            raise ZoteroError(
                "Could not connect to the Zotero API after all retries."
            ) from last_transport_error
        raise ZoteroError("Zotero API request failed after all retries.")

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise ZoteroError("Zotero returned an invalid JSON response.") from exc

    async def _items_page(self, path: str, params: dict[str, Any]) -> ZoteroPage:
        response = await self._request(path, params=params)
        payload = self._json(response)
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise ZoteroError("Zotero returned an unexpected items response.")
        try:
            total_results = int(response.headers.get("Total-Results", len(payload)))
        except ValueError:
            total_results = len(payload)
        return ZoteroPage(
            items=cast(list[dict[str, Any]], payload), total_results=total_results
        )

    async def search_items(
        self, query: str, *, start: int = 0, limit: int = 100
    ) -> ZoteroPage:
        """Search all item fields and indexed full-text content."""

        return await self._items_page(
            self._items_path,
            {
                "format": "json",
                "include": "data",
                "q": query,
                "qmode": "everything",
                "sort": "dateModified",
                "direction": "desc",
                "start": start,
                "limit": limit,
            },
        )

    async def get_item(self, key: str) -> dict[str, Any]:
        """Get one item by key."""

        response = await self._request(
            f"{self._items_path}/{key}", params={"format": "json"}
        )
        payload = self._json(response)
        if not isinstance(payload, dict):
            raise ZoteroError("Zotero returned an unexpected item response.")
        return cast(dict[str, Any], payload)

    async def get_items_by_keys(self, keys: list[str]) -> list[dict[str, Any]]:
        """Get items in batches of the API's 50-key maximum."""

        unique_keys = list(dict.fromkeys(keys))
        results: list[dict[str, Any]] = []
        for position in range(0, len(unique_keys), 50):
            batch = unique_keys[position : position + 50]
            page = await self._items_page(
                self._items_path,
                {
                    "format": "json",
                    "include": "data",
                    "itemKey": ",".join(batch),
                    "limit": 50,
                },
            )
            results.extend(page.items)
        return results

    async def get_attachments(self, item_key: str) -> list[dict[str, Any]]:
        """Get all attachment children for a bibliographic item."""

        path = f"{self._items_path}/{item_key}/children"
        start = 0
        results: list[dict[str, Any]] = []
        while True:
            page = await self._items_page(
                path,
                {
                    "format": "json",
                    "include": "data",
                    "itemType": "attachment",
                    "start": start,
                    "limit": 100,
                },
            )
            results.extend(page.items)
            start += len(page.items)
            if not page.items or start >= page.total_results:
                return results

    async def get_fulltext(self, attachment_key: str) -> dict[str, Any]:
        """Get Zotero's synchronized full-text content for an attachment."""

        response = await self._request(f"{self._items_path}/{attachment_key}/fulltext")
        payload = self._json(response)
        if not isinstance(payload, dict):
            raise ZoteroError("Zotero returned an unexpected full-text response.")
        return cast(dict[str, Any], payload)
