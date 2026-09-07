"""Application logic for the Zotero MCP tools."""

import re
from dataclasses import dataclass
from typing import Any, Literal

from .models import (
    Attachment,
    Paper,
    PaperText,
    SearchHit,
    SearchResults,
    TextSegment,
)
from .normalize import (
    attachment_from_item,
    creators_from_item,
    is_child_item,
    is_pdf_attachment,
    item_data,
    item_key,
    item_type,
    paper_summary,
    parent_item_key,
)
from .zotero_client import ZoteroClient, ZoteroNotFoundError

_ITEM_KEY_PATTERN = re.compile(r"^[A-Z0-9]{8}$")
_MATCH_TYPE = Literal["paper", "attachment", "note", "annotation"]


@dataclass(frozen=True, slots=True)
class _ResolvedHit:
    root_item: dict[str, Any]
    matched_by: _MATCH_TYPE
    attachment_key: str | None


@dataclass(slots=True)
class _AccumulatedHit:
    item: dict[str, Any]
    matched_by: list[_MATCH_TYPE]
    attachment_keys: list[str]


@dataclass(slots=True)
class _ExcerptWindow:
    start: int
    end: int
    first_match_start: int
    first_match_end: int


class ZoteroService:
    """Implement stateless search and full-text retrieval."""

    def __init__(
        self,
        client: ZoteroClient,
        *,
        max_text_characters: int = 12_000,
        max_search_pages: int = 20,
    ) -> None:
        self.client = client
        self.max_text_characters = max_text_characters
        self.max_search_pages = max_search_pages

    @staticmethod
    def _key(value: str, name: str = "item_key") -> str:
        key = value.strip().upper()
        if not _ITEM_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"{name} must be an 8-character Zotero item key.")
        return key

    async def _resolve_hits(self, hits: list[dict[str, Any]]) -> list[_ResolvedHit]:
        known = {key: item for item in hits if (key := item_key(item))}

        # Notes and annotations can add another parent level. Fetch at most four
        # levels so malformed parent cycles cannot cause an unbounded request loop.
        for _ in range(4):
            missing = list(
                dict.fromkeys(
                    parent
                    for item in known.values()
                    if (parent := parent_item_key(item)) and parent not in known
                )
            )
            if not missing:
                break
            fetched = await self.client.get_items_by_keys(missing)
            added = False
            for item in fetched:
                key = item_key(item)
                if key and key not in known:
                    known[key] = item
                    added = True
            if not added:
                break

        resolved: list[_ResolvedHit] = []
        for hit in hits:
            original_type = item_type(hit)
            match_type: _MATCH_TYPE
            if original_type in {"attachment", "note", "annotation"}:
                match_type = original_type  # type: ignore[assignment]
            else:
                match_type = "paper"

            current = hit
            attachment_key: str | None = None
            visited: set[str] = set()
            for _ in range(5):
                current_key = item_key(current)
                if not current_key or current_key in visited:
                    break
                visited.add(current_key)
                if item_type(current) == "attachment" and attachment_key is None:
                    attachment_key = current_key
                if not is_child_item(current):
                    resolved.append(
                        _ResolvedHit(
                            root_item=current,
                            matched_by=match_type,
                            attachment_key=attachment_key,
                        )
                    )
                    break
                parent = parent_item_key(current)
                if not parent or parent not in known:
                    break
                current = known[parent]

        return resolved

    async def search_papers(
        self, query: str, *, limit: int = 20, offset: int = 0
    ) -> SearchResults:
        """Search Zotero metadata and indexed full text, then return parent papers."""

        query = query.strip()
        if not query:
            raise ValueError("query must not be empty.")
        if len(query) > 500:
            raise ValueError("query must not exceed 500 characters.")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50.")
        if not 0 <= offset <= 500:
            raise ValueError("offset must be between 0 and 500.")

        needed = offset + limit
        order: list[str] = []
        accumulated: dict[str, _AccumulatedHit] = {}
        raw_start = 0
        raw_total = 0
        scanned = 0
        pages = 0
        scan_limit_reached = False

        while len(order) < needed:
            if pages >= self.max_search_pages:
                scan_limit_reached = raw_start < raw_total
                break

            page = await self.client.search_items(query, start=raw_start, limit=100)
            pages += 1
            raw_total = page.total_results
            scanned += len(page.items)
            if not page.items:
                break
            raw_start += len(page.items)

            for resolved in await self._resolve_hits(page.items):
                root_key = item_key(resolved.root_item)
                if not root_key:
                    continue
                entry = accumulated.get(root_key)
                if entry is None:
                    entry = _AccumulatedHit(
                        item=resolved.root_item,
                        matched_by=[],
                        attachment_keys=[],
                    )
                    accumulated[root_key] = entry
                    order.append(root_key)
                if resolved.matched_by not in entry.matched_by:
                    entry.matched_by.append(resolved.matched_by)
                if (
                    resolved.attachment_key
                    and resolved.attachment_key not in entry.attachment_keys
                ):
                    entry.attachment_keys.append(resolved.attachment_key)

            if raw_start >= raw_total:
                break

        selected_keys = order[offset : offset + limit]
        papers = [
            SearchHit(
                paper=paper_summary(accumulated[key].item),
                matched_by=accumulated[key].matched_by,
                matched_attachment_keys=accumulated[key].attachment_keys,
            )
            for key in selected_keys
        ]
        more_results = len(order) > offset + limit or raw_start < raw_total

        return SearchResults(
            query=query,
            offset=offset,
            limit=limit,
            returned=len(papers),
            zotero_total_results=raw_total,
            scanned_zotero_items=scanned,
            more_results_available=more_results,
            next_offset=offset + len(papers) if papers and more_results else None,
            scan_limit_reached=scan_limit_reached,
            papers=papers,
        )

    async def get_paper(self, item_key_value: str) -> Paper:
        """Get all metadata and attachment descriptions for one paper."""

        key = self._key(item_key_value)
        item = await self.client.get_item(key)
        if is_child_item(item):
            raise ValueError(
                "item_key must identify a bibliographic item, not a child item."
            )

        attachment_items = await self.client.get_attachments(key)
        attachments = [attachment_from_item(value) for value in attachment_items]
        return Paper(
            summary=paper_summary(item),
            creators=creators_from_item(item),
            attachments=attachments,
            metadata=dict(item_data(item)),
        )

    @staticmethod
    def _integer(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _excerpt_segments(
        content: str,
        query: str,
        *,
        offset: int,
        budget: int,
        context_characters: int = 240,
    ) -> tuple[list[TextSegment], int, int | None]:
        matches = list(
            re.compile(re.escape(query), re.IGNORECASE).finditer(content, offset)
        )
        if not matches:
            return [], 0, None

        windows: list[_ExcerptWindow] = []
        for match in matches:
            start = max(offset, match.start() - context_characters)
            end = min(len(content), match.end() + context_characters)
            if windows and start <= windows[-1].end:
                windows[-1].end = max(windows[-1].end, end)
            else:
                windows.append(
                    _ExcerptWindow(
                        start=start,
                        end=end,
                        first_match_start=match.start(),
                        first_match_end=match.end(),
                    )
                )

        segments: list[TextSegment] = []
        remaining = budget
        next_offset: int | None = None
        for index, window in enumerate(windows):
            if remaining <= 0:
                next_offset = windows[index].first_match_start
                break

            start = window.start
            end = window.end
            if end - start > remaining:
                center = (window.first_match_start + window.first_match_end) // 2
                start = max(window.start, center - remaining // 2)
                end = min(window.end, start + remaining)
                start = max(window.start, end - remaining)
                next_offset = window.first_match_end

            segments.append(TextSegment(start=start, end=end, text=content[start:end]))
            remaining -= end - start
            if next_offset is not None:
                break
            if index + 1 < len(windows) and remaining <= 0:
                next_offset = windows[index + 1].first_match_start
                break

        return segments, len(matches), next_offset

    async def get_paper_text(
        self,
        item_key_value: str,
        *,
        attachment_key_value: str | None = None,
        offset: int = 0,
        max_characters: int | None = None,
        query: str | None = None,
    ) -> PaperText:
        """Read synchronized Zotero full text without downloading the attachment."""

        item_key_value = self._key(item_key_value)
        if offset < 0:
            raise ValueError("offset must be zero or greater.")
        if max_characters is None:
            max_characters = self.max_text_characters
        if not 100 <= max_characters <= self.max_text_characters:
            raise ValueError(
                f"max_characters must be between 100 and {self.max_text_characters}."
            )
        if query is not None:
            query = query.strip()
            if not query:
                raise ValueError("query must not be empty when provided.")
            if len(query) > 500:
                raise ValueError("query must not exceed 500 characters.")

        attachment_items = await self.client.get_attachments(item_key_value)
        attachments = [attachment_from_item(value) for value in attachment_items]
        pdfs = [
            attachment for attachment in attachments if is_pdf_attachment(attachment)
        ]

        selected: Attachment | None = None
        if attachment_key_value is not None:
            attachment_key_value = self._key(attachment_key_value, "attachment_key")
            selected = next(
                (
                    attachment
                    for attachment in pdfs
                    if attachment.key == attachment_key_value
                ),
                None,
            )
            if selected is None:
                raise ValueError(
                    "attachment_key must identify a PDF attachment that belongs to this paper."
                )
        elif len(pdfs) == 1:
            selected = pdfs[0]
        elif len(pdfs) > 1:
            return PaperText(
                status="selection_required",
                item_key=item_key_value,
                available_attachments=pdfs,
                message="This paper has multiple PDF attachments. Select an attachment_key.",
            )
        else:
            return PaperText(
                status="not_available",
                item_key=item_key_value,
                available_attachments=[],
                message="This paper has no PDF attachment.",
            )

        try:
            fulltext = await self.client.get_fulltext(selected.key)
        except ZoteroNotFoundError:
            return PaperText(
                status="not_available",
                item_key=item_key_value,
                attachment_key=selected.key,
                available_attachments=pdfs,
                message="Zotero has no synchronized full text for this PDF attachment.",
            )

        content_value = fulltext.get("content")
        content = content_value if isinstance(content_value, str) else ""
        segments: list[TextSegment]
        match_count: int | None = None
        next_offset: int | None
        message: str | None = None

        if query is None:
            start = min(offset, len(content))
            end = min(len(content), start + max_characters)
            segments = [TextSegment(start=start, end=end, text=content[start:end])]
            next_offset = end if end < len(content) else None
        else:
            segments, match_count, next_offset = self._excerpt_segments(
                content,
                query,
                offset=min(offset, len(content)),
                budget=max_characters,
            )
            if match_count == 0:
                message = "The literal query was not found in this attachment's synchronized text."

        return PaperText(
            status="ok",
            item_key=item_key_value,
            attachment_key=selected.key,
            available_attachments=pdfs,
            segments=segments,
            content_characters=len(content),
            indexed_pages=self._integer(fulltext.get("indexedPages")),
            total_pages=self._integer(fulltext.get("totalPages")),
            indexed_characters=self._integer(fulltext.get("indexedChars")),
            total_characters=self._integer(fulltext.get("totalChars")),
            query=query,
            match_count=match_count,
            next_offset=next_offset,
            message=message,
        )
