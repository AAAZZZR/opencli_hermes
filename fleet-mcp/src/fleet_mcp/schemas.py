"""Pydantic models for MCP tool I/O and fleet-hub API shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# fleet-hub domain models
# ---------------------------------------------------------------------------

class HubNode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    label: str
    status: str = "offline"           # online | offline
    mode: str | None = None           # bridge | cdp
    os: str | None = None
    logged_in_sites: list[str] = Field(default_factory=list)
    opencli_version: str | None = None
    last_seen_at: datetime | None = None
    created_at: datetime | None = None


class HubTaskResult(BaseModel):
    """Response from POST /api/v1/tasks when wait=true."""
    model_config = ConfigDict(extra="ignore")
    id: str
    node_id: str
    site: str
    command: str
    status: str                       # pending | running | completed | failed | timeout | cancelled
    error_code: str | None = None
    error_message: str | None = None
    exit_code: int | None = None
    items_total: int = 0
    items_stored: int = 0
    duration_ms: int | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class HubRecordList(BaseModel):
    items: list[dict[str, Any]]
    total: int


# ---------------------------------------------------------------------------
# MCP tool outputs
# ---------------------------------------------------------------------------

class NodeInfo(BaseModel):
    node_id: str
    label: str
    online: bool
    last_seen: datetime | None = None
    logged_in_sites: list[str] = Field(default_factory=list)
    chrome_mode: str | None = None
    os: str | None = None
    opencli_version: str | None = None


class SiteInfo(BaseModel):
    site: str
    commands: list[str]
    description: str


class DispatchResult(BaseModel):
    success: bool
    node_id: str | None = None
    task_id: str | None = None
    items: list[dict] = Field(default_factory=list)
    truncated: bool = False
    total_items: int = 0
    duration_ms: int | None = None
    error: str | None = None
    error_code: str | None = None
    exit_code: int | None = None


class BroadcastNodeResult(BaseModel):
    node_id: str
    success: bool
    items: list[dict] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None


class BroadcastResult(BaseModel):
    total_nodes: int
    results: list[BroadcastNodeResult] = Field(default_factory=list)


class TaskStatusResult(BaseModel):
    task_id: str
    status: str
    items: list[dict] = Field(default_factory=list)
    total_items: int = 0
    error: str | None = None
    error_code: str | None = None
