from typing import Any

import pytest

from mcp_zotero.service import ZoteroService
from mcp_zotero.zotero_client import ZoteroNotFoundError, ZoteroPage

pytestmark = pytest.mark.anyio


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


@pytest.fixture
def paper() -> dict[str, Any]:
    return zotero_item(
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


@pytest.fixture
def other_paper() -> dict[str, Any]:
    return zotero_item("PAPER002", "conferencePaper", title="Another paper")


@pytest.fixture
def pdf() -> dict[str, Any]:
    return zotero_item(
        "ATTACH01",
        "attachment",
        title="Full Text PDF",
        parentItem="PAPER001",
        contentType="application/pdf",
        filename="paper.pdf",
        linkMode="imported_file",
    )


@pytest.fixture
def note() -> dict[str, Any]:
    return zotero_item(
        "NOTE0001",
        "note",
        title="",
        parentItem="PAPER001",
    )


class FakeClient:
    def __init__(
        self,
        paper: dict[str, Any],
        other_paper: dict[str, Any],
        pdf: dict[str, Any],
    ) -> None:
        self.search_pages: list[ZoteroPage] = []
        self.items = {"PAPER001": paper, "PAPER002": other_paper}
        self.attachments = {"PAPER001": [pdf], "PAPER002": []}
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


@pytest.fixture
def fake_client(
    paper: dict[str, Any],
    other_paper: dict[str, Any],
    pdf: dict[str, Any],
) -> FakeClient:
    return FakeClient(paper, other_paper, pdf)


@pytest.fixture
def service(fake_client: FakeClient) -> ZoteroService:
    return ZoteroService(fake_client, max_text_characters=500)  # type: ignore


async def test_search_resolves_children_and_deduplicates_parent(
    service: ZoteroService,
    fake_client: FakeClient,
    pdf: dict[str, Any],
    note: dict[str, Any],
    other_paper: dict[str, Any],
) -> None:
    fake_client.search_pages = [
        ZoteroPage(items=[pdf, note, other_paper], total_results=3)
    ]

    result = await service.search_papers("test")

    assert result.returned == 2
    assert [hit.paper.key for hit in result.papers] == ["PAPER001", "PAPER002"]
    assert result.papers[0].matched_by == ["attachment", "note"]
    assert result.papers[0].matched_attachment_keys == ["ATTACH01"]
    assert result.papers[0].paper.authors == ["Ada Lovelace"]
    assert result.papers[0].paper.year == 2024
    assert result.more_results_available is False


async def test_get_paper_includes_metadata_creators_and_attachments(
    service: ZoteroService,
) -> None:
    result = await service.get_paper("paper001")

    assert result.summary.title == "A test paper"
    assert result.metadata["DOI"] == "10.1000/test"
    assert [creator.name for creator in result.creators] == [
        "Ada Lovelace",
        "Research Group",
    ]
    assert [attachment.key for attachment in result.attachments] == ["ATTACH01"]


async def test_get_paper_text_returns_contiguous_bounded_text(
    service: ZoteroService,
) -> None:
    result = await service.get_paper_text("PAPER001", offset=6, max_characters=100)

    assert result.status == "ok"
    assert result.attachment_key == "ATTACH01"
    assert result.segments[0].start == 6
    assert result.segments[0].text.startswith("before")
    assert result.content_characters == 57
    assert result.indexed_pages == 10


async def test_get_paper_text_returns_literal_excerpts(
    service: ZoteroService,
) -> None:
    result = await service.get_paper_text(
        "PAPER001",
        max_characters=100,
        query="important phrase",
    )

    assert result.status == "ok"
    assert result.match_count == 1
    assert "Important phrase" in result.segments[0].text


async def test_get_paper_text_requires_selection_for_multiple_pdfs(
    service: ZoteroService,
    fake_client: FakeClient,
    pdf: dict[str, Any],
) -> None:
    second_pdf = zotero_item(
        "ATTACH02",
        "attachment",
        parentItem="PAPER001",
        contentType="application/pdf",
        filename="supplement.pdf",
    )
    fake_client.attachments["PAPER001"] = [pdf, second_pdf]

    result = await service.get_paper_text("PAPER001")

    assert result.status == "selection_required"
    assert [item.key for item in result.available_attachments] == [
        "ATTACH01",
        "ATTACH02",
    ]


async def test_get_paper_text_handles_missing_synchronized_text(
    service: ZoteroService,
    fake_client: FakeClient,
) -> None:
    fake_client.fulltext["ATTACH01"] = ZoteroNotFoundError("missing")

    result = await service.get_paper_text("PAPER001")

    assert result.status == "not_available"
    assert "no synchronized full text" in (result.message or "")


async def test_rejects_invalid_item_key(service: ZoteroService) -> None:
    with pytest.raises(ValueError, match="8-character"):
        await service.get_paper("not-a-key")
