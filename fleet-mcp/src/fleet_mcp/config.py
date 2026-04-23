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

    # opencli-admin backend
    admin_api_url: str = "http://localhost:8031"

    # Security
    rate_limit_per_node: int = 10  # requests per minute
    rate_limit_global: int = 60  # requests per minute
    audit_log_path: Path = Path("~/.fleet-mcp/audit.log")
    max_items_inline: int = 50  # truncate above this in tool responses

    # Timeouts
    task_timeout_sec: int = 120
    broadcast_timeout_sec: int = 180
    task_poll_interval_sec: float = 2.0

    # Node-site mapping (Phase 1)
    node_sites_path: Path = Path("node_sites.yaml")

    # Logging
    log_level: str = "INFO"

    @field_validator("audit_log_path", mode="before")
    @classmethod
    def expand_home(cls, v: str | Path) -> Path:
        return Path(v).expanduser()


settings = Settings()
