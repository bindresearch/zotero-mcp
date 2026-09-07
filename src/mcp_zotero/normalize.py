"""Convert Zotero API objects into compact MCP result models."""

import re
from typing import Any

from .models import Attachment, Creator, PaperSummary

_CHILD_ITEM_TYPES = frozenset({"attachment", "note", "annotation"})
_YEAR_PATTERN = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def item_data(item: dict[str, Any]) -> dict[str, Any]:
    """Return the writable data object from a Zotero JSON response."""

    data = item.get("data")
    return data if isinstance(data, dict) else item


def item_key(item: dict[str, Any]) -> str | None:
    """Return an item's key."""

    key = item.get("key") or item_data(item).get("key")
    return key if isinstance(key, str) and key else None


def item_type(item: dict[str, Any]) -> str:
    """Return an item's Zotero item type."""

    value = item_data(item).get("itemType")
    return value if isinstance(value, str) else ""


def parent_item_key(item: dict[str, Any]) -> str | None:
    """Return an item's parent key, if present."""

    value = item_data(item).get("parentItem")
    return value if isinstance(value, str) and value else None


def is_child_item(item: dict[str, Any]) -> bool:
    """Return whether an item is not a bibliographic parent item."""

    return item_type(item) in _CHILD_ITEM_TYPES


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _version(item: dict[str, Any]) -> int | None:
    value = item.get("version", item_data(item).get("version"))
    return value if isinstance(value, int) else None


def creators_from_item(item: dict[str, Any]) -> list[Creator]:
    """Normalize Zotero creator objects."""

    creators = item_data(item).get("creators", [])
    if not isinstance(creators, list):
        return []

    result: list[Creator] = []
    for value in creators:
        if not isinstance(value, dict):
            continue
        creator_type = _optional_string(value.get("creatorType")) or "contributor"
        first_name = _optional_string(value.get("firstName"))
        last_name = _optional_string(value.get("lastName"))
        institutional_name = _optional_string(value.get("name"))
        name = institutional_name or " ".join(
            part for part in (first_name, last_name) if part
        )
        if not name:
            continue
        result.append(
            Creator(
                creator_type=creator_type,
                name=name,
                first_name=first_name,
                last_name=last_name,
            )
        )
    return result


def _year(date: str | None) -> int | None:
    if not date:
        return None
    match = _YEAR_PATTERN.search(date)
    if not match:
        return None
    year = int(match.group(1))
    return year if 1000 <= year <= 2999 else None


def _tags(data: dict[str, Any]) -> list[str]:
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        return []
    values: list[str] = []
    for tag in tags:
        value = tag.get("tag") if isinstance(tag, dict) else tag
        normalized = _optional_string(value)
        if normalized:
            values.append(normalized)
    return values


def _alternate_url(item: dict[str, Any]) -> str | None:
    links = item.get("links")
    if not isinstance(links, dict):
        return None
    alternate = links.get("alternate")
    if not isinstance(alternate, dict):
        return None
    return _optional_string(alternate.get("href"))


def paper_summary(
    item: dict[str, Any], *, max_abstract_chars: int = 2_000
) -> PaperSummary:
    """Create a compact summary from a bibliographic Zotero item."""

    data = item_data(item)
    key = item_key(item)
    if not key:
        raise ValueError("Zotero returned an item without a key.")

    date = _optional_string(data.get("date"))
    abstract = _optional_string(data.get("abstractNote"))
    if abstract and len(abstract) > max_abstract_chars:
        abstract = abstract[: max_abstract_chars - 1].rstrip() + "…"

    creators = creators_from_item(item)
    authors = [creator.name for creator in creators if creator.creator_type == "author"]

    return PaperSummary(
        key=key,
        version=_version(item),
        item_type=item_type(item),
        title=_optional_string(data.get("title")) or "(untitled)",
        authors=authors,
        year=_year(date),
        date=date,
        abstract=abstract,
        publication_title=_optional_string(data.get("publicationTitle")),
        doi=_optional_string(data.get("DOI")),
        url=_optional_string(data.get("url")),
        tags=_tags(data),
        date_modified=_optional_string(data.get("dateModified")),
        zotero_url=_alternate_url(item),
    )


def attachment_from_item(item: dict[str, Any]) -> Attachment:
    """Create an attachment description from a Zotero attachment item."""

    data = item_data(item)
    key = item_key(item)
    if not key:
        raise ValueError("Zotero returned an attachment without a key.")

    return Attachment(
        key=key,
        version=_version(item),
        title=_optional_string(data.get("title")) or "(untitled attachment)",
        filename=_optional_string(data.get("filename")),
        content_type=_optional_string(data.get("contentType")),
        link_mode=_optional_string(data.get("linkMode")),
        url=_optional_string(data.get("url")),
        parent_item=parent_item_key(item),
        date_modified=_optional_string(data.get("dateModified")),
    )


def is_pdf_attachment(attachment: Attachment) -> bool:
    """Return whether an attachment represents a PDF."""

    if attachment.content_type and attachment.content_type.lower() == "application/pdf":
        return True
    return bool(attachment.filename and attachment.filename.lower().endswith(".pdf"))
