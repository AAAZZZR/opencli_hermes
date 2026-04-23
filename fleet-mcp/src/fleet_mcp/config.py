"""Configuration loaded from environment / .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # fleet-hub URL — typically localhost on the VPS
    hub_url: str = "http://localhost:8031"

    # Security
    rate_limit_per_node: int = 10   # requests per minute
    rate_limit_global: int = 60     # requests per minute
    audit_log_path: Path = Path("~/.fleet-mcp/audit.log")
    max_items_inline: int = 50      # truncate above this in tool responses

    # Timeouts
    task_timeout_sec: int = 120
    broadcast_timeout_sec: int = 180

    # Logging
    log_level: str = "INFO"

    @field_validator("audit_log_path", mode="before")
    @classmethod
    def expand_home(cls, v: str | Path) -> Path:
        return Path(v).expanduser()

    @field_validator("hub_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


settings = Settings()
