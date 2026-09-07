> [!WARNING]
> This is entirely vibe-coded (but human-reviewed)

# MCP Zotero

This project provides read-only MCP access to one Zotero group library. It uses the Zotero Web API v3 and can run over Streamable HTTP or stdio.

The server is stateless. It does not keep a local index. It does not download or parse PDF files. Zotero performs the search and supplies synchronized attachment text.

## Tools

### `search_papers`

Search Zotero with `qmode=everything`. The server resolves attachment, note, and annotation results to their parent bibliographic items. It then removes duplicate papers.

The Zotero Web API currently supports phrase search. For broad discovery, use separate short queries and synonyms. Zotero does not provide a relevance sort for item requests, so results use the most recently modified items first. Zotero's result count is a count of raw matching items, not unique parent papers.

### `get_paper`

Get all metadata and the attachment list for one bibliographic item.

### `get_paper_text`

Get synchronized text from Zotero's `/items/{attachmentKey}/fulltext` endpoint. The tool does not request the attachment file.

The tool returns a bounded character range by default. Supply `query` to get excerpts around literal matches. Zotero full text has no reliable page boundaries, so the tool uses character offsets.

## Requirements

- Python 3.13 or later
- Zotero group ID (find from the URL)
- A read-only Zotero API key with access to the group and its files

Create a dedicated key in your Zotero account settings. The MCP endpoint has no authentication and must stay on a private network.

## Configuration

Copy the example configuration:

```sh
cp .env.example .env
```

Set these required values:

```env
ZOTERO_GROUP_ID=123456
ZOTERO_API_KEY=replace-with-a-read-only-api-key
```

The main optional settings are:

```env
MCP_TRANSPORT=streamable-http  # or stdio
MCP_HOST=127.0.0.1
MCP_PORT=8000
MCP_LOG_LEVEL=INFO
MCP_MAX_TEXT_CHARACTERS=12000
ZOTERO_TIMEOUT_SECONDS=30
ZOTERO_MAX_RETRIES=3
ZOTERO_MAX_SEARCH_PAGES=20
```

## Install and run

Install the project with `uv`:

```sh
uv sync
```

Start the server. The default is Streamable HTTP:

```sh
uv run mcp-zotero
```

Set `MCP_TRANSPORT=stdio` in `.env` to run over standard input/output instead:

```sh
MCP_TRANSPORT=stdio uv run mcp-zotero
```

### Streamable HTTP

The MCP endpoint is:

```text
http://127.0.0.1:8000/mcp
```

Point the LiteLLM MCP server configuration at this URL. No MCP authorization header is required. Use `MCP_HOST=0.0.0.0` when LiteLLM connects from another container or host on the private network.

### stdio

Run in stdio mode for a local MCP client that launches the process directly.

For interactive development, use the MCP Inspector:

```sh
uv run mcp dev src/mcp_zotero/server.py
```

## Test

```sh
uv run pytest
```

## API behavior

The client sends these headers to Zotero:

```text
Zotero-API-Key: ...
Zotero-API-Version: 3
```

It observes Zotero `Backoff` and `Retry-After` headers. It sends no more than four concurrent Zotero requests. Parent items are retrieved in batches of at most 50 keys, as required by the API.

A PDF can have no synchronized text even when its attachment metadata exists. In this case, `get_paper_text` returns `status="not_available"`.

## Zotero documentation

- [Web API v3 basics](https://www.zotero.org/support/dev/web_api/v3/basics)
- [Full-text content requests](https://www.zotero.org/support/dev/web_api/v3/fulltext_content)
