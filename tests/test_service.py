import asyncio
from typing import Any

import pytest

from mcp_zotero.service import ZoteroService
from mcp_zotero.zotero_client import ZoteroNotFoundError, ZoteroPage


def zotero_item(key: str, item_type: str, **data: Any) -> dict[str, Any]:
    fields = {
        "key": key,
        "version": 1,
        "itemType": item_type,
        "title": data.pop("title", key),
        **data,
    }
    return {
        "key": key,
        "version": 1,
        "links": {"alternate": {"href": f"https://www.zotero.org/item/{key}"}},
        "data": fields,
    }


PAPER = zotero_item(
    "PAPER001",
    "journalArticle",
    title="A test paper",
    creators=[
        {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"},
        {"creatorType": "editor", "name": "Research Group"},
    ],
    date="2024-05-10",
    abstractNote="An abstract",
    publicationTitle="Test Journal",
    DOI="10.1000/test",
    tags=[{"tag": "example"}],
)
OTHER_PAPER = zotero_item("PAPER002", "conferencePaper", title="Another paper")
PDF = zotero_item(
    "ATTACH01",
    "attachment",
    title="Full Text PDF",
    parentItem="PAPER001",
    contentType="application/pdf",
    filename="paper.pdf",
    linkMode="imported_file",
)
NOTE = zotero_item(
    "NOTE0001",
    "note",
    title="",
    parentItem="PAPER001",
)


class FakeClient:
    def __init__(self) -> None:
        self.search_pages: list[ZoteroPage] = []
        self.items = {"PAPER001": PAPER, "PAPER002": OTHER_PAPER}
        self.attachments = {"PAPER001": [PDF], "PAPER002": []}
        self.fulltext: dict[str, dict[str, Any] | Exception] = {
            "ATTACH01": {
                "content": "Alpha before. Important phrase in the paper. Omega after.",
                "indexedPages": 10,
                "totalPages": 10,
            }
        }

    async def search_items(self, query: str, *, start: int, limit: int) -> ZoteroPage:
        del query, start, limit
        return self.search_pages.pop(0)

    async def get_items_by_keys(self, keys: list[str]) -> list[dict[str, Any]]:
        return [self.items[key] for key in keys if key in self.items]

    async def get_item(self, key: str) -> dict[str, Any]:
        return self.items[key]

    async def get_attachments(self, key: str) -> list[dict[str, Any]]:
        return self.attachments[key]

    async def get_fulltext(self, key: str) -> dict[str, Any]:
        value = self.fulltext[key]
        if isinstance(value, Exception):
            raise value
        return value


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def test_search_resolves_children_and_deduplicates_parent() -> None:
    client = FakeClient()
    client.search_pages = [ZoteroPage(items=[PDF, NOTE, OTHER_PAPER], total_results=3)]
    service = ZoteroService(client)  # type: ignore[arg-type]

    result = run(service.search_papers("test"))

    assert result.returned == 2
    assert [hit.paper.key for hit in result.papers] == ["PAPER001", "PAPER002"]
    assert result.papers[0].matched_by == ["attachment", "note"]
    assert result.papers[0].matched_attachment_keys == ["ATTACH01"]
    assert result.papers[0].paper.authors == ["Ada Lovelace"]
    assert result.papers[0].paper.year == 2024
    assert result.more_results_available is False


def test_get_paper_includes_metadata_creators_and_attachments() -> None:
    service = ZoteroService(FakeClient())  # type: ignore[arg-type]

    result = run(service.get_paper("paper001"))

    assert result.summary.title == "A test paper"
    assert result.metadata["DOI"] == "10.1000/test"
    assert [creator.name for creator in result.creators] == [
        "Ada Lovelace",
        "Research Group",
    ]
    assert [attachment.key for attachment in result.attachments] == ["ATTACH01"]


def test_get_paper_text_returns_contiguous_bounded_text() -> None:
    service = ZoteroService(FakeClient(), max_text_characters=500)  # type: ignore[arg-type]

    result = run(service.get_paper_text("PAPER001", offset=6, max_characters=100))

    assert result.status == "ok"
    assert result.attachment_key == "ATTACH01"
    assert result.segments[0].start == 6
    assert result.segments[0].text.startswith("before")
    assert result.content_characters == 57
    assert result.indexed_pages == 10


def test_get_paper_text_returns_literal_excerpts() -> None:
    service = ZoteroService(FakeClient(), max_text_characters=500)  # type: ignore[arg-type]

    result = run(
        service.get_paper_text(
            "PAPER001",
            max_characters=100,
            query="important phrase",
        )
    )

    assert result.status == "ok"
    assert result.match_count == 1
    assert "Important phrase" in result.segments[0].text


def test_get_paper_text_requires_selection_for_multiple_pdfs() -> None:
    client = FakeClient()
    second_pdf = zotero_item(
        "ATTACH02",
        "attachment",
        parentItem="PAPER001",
        contentType="application/pdf",
        filename="supplement.pdf",
    )
    client.attachments["PAPER001"] = [PDF, second_pdf]
    service = ZoteroService(client)  # type: ignore[arg-type]

    result = run(service.get_paper_text("PAPER001"))

    assert result.status == "selection_required"
    assert [item.key for item in result.available_attachments] == [
        "ATTACH01",
        "ATTACH02",
    ]


def test_get_paper_text_handles_missing_synchronized_text() -> None:
    client = FakeClient()
    client.fulltext["ATTACH01"] = ZoteroNotFoundError("missing")
    service = ZoteroService(client)  # type: ignore[arg-type]

    result = run(service.get_paper_text("PAPER001"))

    assert result.status == "not_available"
    assert "no synchronized full text" in (result.message or "")


def test_rejects_invalid_item_key() -> None:
    service = ZoteroService(FakeClient())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="8-character"):
        run(service.get_paper("not-a-key"))
