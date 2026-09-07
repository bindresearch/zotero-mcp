"""Structured results returned by the MCP tools."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ResultModel(BaseModel):
    """Base model for MCP results."""

    model_config = ConfigDict(extra="forbid")


class Creator(ResultModel):
    creator_type: str
    name: str
    first_name: str | None = None
    last_name: str | None = None


class PaperSummary(ResultModel):
    key: str
    version: int | None = None
    item_type: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    date: str | None = None
    abstract: str | None = None
    publication_title: str | None = None
    doi: str | None = None
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    date_modified: str | None = None
    zotero_url: str | None = None


class Attachment(ResultModel):
    key: str
    version: int | None = None
    title: str
    filename: str | None = None
    content_type: str | None = None
    link_mode: str | None = None
    url: str | None = None
    parent_item: str | None = None
    date_modified: str | None = None


class Paper(ResultModel):
    summary: PaperSummary
    creators: list[Creator] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(
        description="All metadata fields returned in the Zotero item's data object."
    )


class SearchHit(ResultModel):
    paper: PaperSummary
    matched_by: list[Literal["paper", "attachment", "note", "annotation"]]
    matched_attachment_keys: list[str] = Field(default_factory=list)


class SearchResults(ResultModel):
    query: str
    offset: int
    limit: int
    returned: int
    zotero_total_results: int
    scanned_zotero_items: int
    more_results_available: bool
    next_offset: int | None = None
    scan_limit_reached: bool = False
    papers: list[SearchHit] = Field(default_factory=list)


class TextSegment(ResultModel):
    start: int
    end: int
    text: str


class PaperText(ResultModel):
    status: Literal["ok", "selection_required", "not_available"]
    item_key: str
    attachment_key: str | None = None
    available_attachments: list[Attachment] = Field(default_factory=list)
    segments: list[TextSegment] = Field(default_factory=list)
    content_characters: int | None = None
    indexed_pages: int | None = None
    total_pages: int | None = None
    indexed_characters: int | None = None
    total_characters: int | None = None
    query: str | None = None
    match_count: int | None = None
    next_offset: int | None = None
    message: str | None = None
