"""fleet-agent config.

Reads env vars (or the file at $FLEET_AGENT_CONFIG) so the systemd/launchd
install can point at ~/.fleet-agent/config.env.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _config_file() -> str:
    """Resolve which .env file to read.

    Priority:
      1. FLEET_AGENT_CONFIG env var (absolute path)
      2. ~/.fleet-agent/config.env
      3. ./.env (dev)
    """
    explicit = os.environ.get("FLEET_AGENT_CONFIG")
    if explicit:
        return explicit
    default = Path.home() / ".fleet-agent" / "config.env"
    if default.exists():
        return str(default)
    return ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_config_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required
    central_url: str = ""
    node_token: str = ""
    node_label: str = ""

    # opencli
    opencli_bin: str = "opencli"
    agent_mode: str = "bridge"  # bridge|cdp

    # WS
    ws_reconnect_min_sec: float = 3.0
    ws_reconnect_max_sec: float = 60.0
    ws_ping_interval_sec: float = 30.0
    ws_ping_timeout_sec: float = 10.0
    # When the WS drops, give in-flight _run_collect tasks up to this many
    # seconds to finish sending their result frames before we cancel them.
    # Prevents losing a scrape result to an orderly hub restart while the
    # agent was mid-`ws.send`.
    ws_shutdown_grace_sec: float = 5.0

    # Login probe
    login_probe_timeout_sec: float = 10.0

    # Logging
    log_level: str = "INFO"

    @property
    def ws_url(self) -> str:
        url = self.central_url.rstrip("/")
        # http(s) → ws(s)
        if url.startswith("https://"):
            return "wss://" + url[len("https://"):] + "/api/v1/nodes/ws"
        if url.startswith("http://"):
            return "ws://" + url[len("http://"):] + "/api/v1/nodes/ws"
        return url + "/api/v1/nodes/ws"


def _detect_os() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform == "win32":
        return "win"
    return "linux"


settings = Settings()
HOST_OS = _detect_os()
