"""Application settings loaded from environment variables."""

from typing import Literal

from pydantic import Field, HttpUrl, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TransportSettings(BaseSettings):
    """Settings needed while the MCP server module is imported."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mcp_transport: Literal["streamable-http", "stdio"] = "streamable-http"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8000, ge=1, le=65535)
    mcp_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class Settings(TransportSettings):
    """Runtime configuration for Zotero access and response limits."""

    zotero_group_id: PositiveInt
    zotero_api_key: SecretStr
    zotero_base_url: HttpUrl = HttpUrl("https://api.zotero.org")
    zotero_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    zotero_max_retries: int = Field(default=3, ge=0, le=10)
    zotero_max_search_pages: int = Field(default=20, ge=1, le=100)
    mcp_max_text_characters: int = Field(default=12_000, ge=500, le=100_000)
