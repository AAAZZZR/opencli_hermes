"""Configuration loaded from environment / .env."""

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

    # Server
    host: str = "0.0.0.0"
    port: int = 8031

    # Database
    database_url: str = "sqlite+aiosqlite:///./fleet_hub.db"

    # Security
    node_token_bytes: int = 32
    audit_log_path: Path = Path("~/.fleet-hub/audit.log")

    # Timeouts
    default_task_timeout_sec: int = 120
    max_task_timeout_sec: int = 600
    ws_ping_interval_sec: int = 30
    ws_ping_timeout_sec: int = 10
    node_offline_after_sec: int = 60

    # Install script substitutions
    public_url: str = "http://localhost:8031"
    opencli_npm_spec: str = "@jackwener/opencli@latest"
    fleet_agent_install_spec: str = (
        "git+https://github.com/YOUR_ORG/opencli_agent.git#subdirectory=fleet-agent"
    )

    # Logging
    log_level: str = "INFO"

    @field_validator("audit_log_path", mode="before")
    @classmethod
    def expand_home(cls, v: str | Path) -> Path:
        return Path(v).expanduser()

    @field_validator("public_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


settings = Settings()
