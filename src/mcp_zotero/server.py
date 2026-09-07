"""MCP server that exposes one Zotero group library."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .models import Paper, PaperText, SearchResults
from .service import ZoteroService
from .settings import Settings, TransportSettings
from .zotero_client import ZoteroClient, ZoteroError


@dataclass(frozen=True, slots=True)
class AppContext:
    service: ZoteroService


_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def create_server(settings: Settings | None = None) -> FastMCP:
    """Create the MCP server. A settings argument is useful for embedded use and tests."""

    transport = settings or TransportSettings()

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[AppContext]:
        runtime_settings = settings or Settings()
        async with ZoteroClient(runtime_settings) as client:
            yield AppContext(
                service=ZoteroService(
                    client,
                    max_text_characters=runtime_settings.mcp_max_text_characters,
                    max_search_pages=runtime_settings.zotero_max_search_pages,
                )
            )

    server = FastMCP(
        "Zotero Group Library",
        instructions=(
            "Search a Zotero group library and read Zotero's synchronized attachment text. "
            "Search is phrase-based. Try separate terms, short phrases, and synonyms when needed."
        ),
        host=transport.mcp_host,
        port=transport.mcp_port,
        log_level=transport.mcp_log_level,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        lifespan=lifespan,
    )

    def service_from(context: Context) -> ZoteroService:
        app_context = context.request_context.lifespan_context
        if not isinstance(app_context, AppContext):
            raise ToolError("The Zotero service is not initialized.")
        return app_context.service

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def search_papers(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=500,
                description=(
                    "A Zotero quick-search phrase. Zotero searches item metadata, notes, tags, "
                    "and indexed full text when qmode=everything is used."
                ),
            ),
        ],
        context: Context,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        offset: Annotated[int, Field(ge=0, le=500)] = 0,
    ) -> SearchResults:
        """Search paper metadata and Zotero-indexed full text with a phrase query.

        Attachment, note, and annotation matches are resolved to their parent paper.
        The result does not claim which attachment field matched. Use get_paper_text
        with a returned attachment key to inspect synchronized PDF text.
        """

        try:
            return await service_from(context).search_papers(
                query, limit=limit, offset=offset
            )
        except (ValueError, ZoteroError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def get_paper(
        item_key: Annotated[
            str,
            Field(description="The 8-character Zotero key of a bibliographic item."),
        ],
        context: Context,
    ) -> Paper:
        """Get a paper's complete Zotero metadata and attachment list."""

        try:
            return await service_from(context).get_paper(item_key)
        except (ValueError, ZoteroError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def get_paper_text(
        item_key: Annotated[
            str,
            Field(description="The 8-character Zotero key of a bibliographic item."),
        ],
        context: Context,
        attachment_key: Annotated[
            str | None,
            Field(
                description=(
                    "A PDF attachment key returned by get_paper. It is required only when the "
                    "paper has multiple PDF attachments."
                )
            ),
        ] = None,
        offset: Annotated[
            int,
            Field(
                ge=0,
                description="Character offset for contiguous text or the start of excerpt search.",
            ),
        ] = 0,
        max_characters: Annotated[
            int | None,
            Field(
                ge=100,
                description=(
                    "Maximum text characters to return. The server-configured maximum is used "
                    "when this value is omitted."
                ),
            ),
        ] = None,
        query: Annotated[
            str | None,
            Field(
                max_length=500,
                description=(
                    "Optional literal phrase. When set, return excerpts around matches instead "
                    "of one contiguous text segment."
                ),
            ),
        ] = None,
    ) -> PaperText:
        """Read bounded text from Zotero's synchronized full-text endpoint.

        This tool does not download or parse the PDF. Zotero does not provide
        reliable page boundaries, so returned segments use character offsets.
        """

        try:
            return await service_from(context).get_paper_text(
                item_key,
                attachment_key_value=attachment_key,
                offset=offset,
                max_characters=max_characters,
                query=query,
            )
        except (ValueError, ZoteroError) as exc:
            raise ToolError(str(exc)) from exc

    return server


mcp = create_server()


def main() -> None:
    """Run the server with the transport configured in MCP_TRANSPORT."""

    mcp.run(transport=TransportSettings().mcp_transport)


if __name__ == "__main__":
    main()
