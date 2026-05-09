"""Application settings.

Settings are loaded from environment variables (and an optional `.env` file).
The model is intentionally strict: unknown env vars are ignored, but typed
fields are validated and bounded.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["json", "text"]
ServiceEnv = Literal["local", "dev", "staging", "prod"]


class Settings(BaseSettings):
    """Top-level configuration for the service.

    Each field maps to an UPPER_SNAKE_CASE env var of the same name.
    `aws_region` additionally accepts the canonical `AWS_REGION` env var.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Service identity ------------------------------------------------------
    service_name: str = Field(default="bedrock-strands-agent")
    service_env: ServiceEnv = Field(default="local")

    # Logging ---------------------------------------------------------------
    log_level: LogLevel = Field(default="INFO")
    log_format: LogFormat = Field(default="json")

    # HTTP ------------------------------------------------------------------
    # Binding 0.0.0.0 inside containers is intentional; suppressed for
    # ruff (S104) and bandit (B104).
    api_host: str = Field(default="0.0.0.0")  # noqa: S104  # nosec B104
    api_port: int = Field(default=8000, ge=1, le=65535)

    # AWS / Bedrock ---------------------------------------------------------
    aws_region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices("aws_region", "AWS_REGION"),
    )
    bedrock_model_id: str = Field(default="us.anthropic.claude-sonnet-4-6-20250514-v1:0")
    bedrock_max_tokens: int = Field(default=4096, ge=1, le=200_000)
    bedrock_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    bedrock_top_p: float = Field(default=0.9, ge=0.0, le=1.0)

    # OpenTelemetry ---------------------------------------------------------
    otel_exporter_otlp_endpoint: str | None = Field(default=None)
    otel_exporter_otlp_insecure: bool = Field(default=True)
    otel_service_name: str | None = Field(default=None)
    strands_otel_enable_console_export: bool = Field(default=False)

    # MCP -------------------------------------------------------------------
    # NoDecode is critical: pydantic-settings v2 JSON-parses list fields
    # from env BEFORE field_validator(mode="before") runs, so a CSV like
    # "filesystem,fetch" would fail. NoDecode hands the raw string to us.
    mcp_enabled_servers: Annotated[list[str], NoDecode] = Field(default_factory=list)
    mcp_config_path: Path = Field(default=Path("mcp.config.json"))

    # ------------------------------------------------------------------ #
    @field_validator("mcp_enabled_servers", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept a CSV string for `MCP_ENABLED_SERVERS`."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # ------------------------------------------------------------------ #
    @property
    def effective_otel_service_name(self) -> str:
        """Return the OTel service name, falling back to `service_name`."""
        return self.otel_service_name or self.service_name

    @property
    def is_otel_enabled(self) -> bool:
        """Whether any tracing exporter is configured."""
        return bool(self.otel_exporter_otlp_endpoint) or self.strands_otel_enable_console_export


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached `Settings` instance."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the `get_settings` cache (test helper)."""
    get_settings.cache_clear()
