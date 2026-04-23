"""Pydantic models for MCP tool I/O and opencli-admin API shapes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# opencli-admin API response envelope
# ---------------------------------------------------------------------------

class AdminMeta(BaseModel):
    total: int | None = None
    page: int | None = None
    limit: int | None = None
    pages: int | None = None


class AdminResponse(BaseModel):
    """Generic wrapper returned by every opencli-admin endpoint."""
    success: bool
    data: dict | list | None = None
    error: str | None = None
    meta: AdminMeta | None = None


# ---------------------------------------------------------------------------
# opencli-admin domain models (partial — only what fleet-mcp needs)
# ---------------------------------------------------------------------------

class AdminNode(BaseModel):
    id: str
    url: str
    label: str | None = None
    protocol: str = "ws"
    mode: str = "cdp"
    node_type: str = "shell"
    status: str = "offline"
    last_seen_at: datetime | None = None


class AdminSource(BaseModel):
    id: str
    name: str
    channel_type: str = "opencli"
    channel_config: dict = Field(default_factory=dict)
    enabled: bool = True


class AdminTask(BaseModel):
    id: str
    source_id: str
    status: str = "pending"
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AdminTaskRun(BaseModel):
    id: str
    task_id: str
    status: str = "pending"
    node_url: str | None = None
    duration_ms: int | None = None
    records_collected: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AdminRecord(BaseModel):
    id: str
    task_id: str | None = None
    source_id: str | None = None
    raw_data: dict | None = None
    normalized_data: dict | None = None
    status: str = "raw"


# ---------------------------------------------------------------------------
# MCP tool outputs
# ---------------------------------------------------------------------------

class NodeInfo(BaseModel):
    node_id: str
    label: str | None = None
    online: bool
    last_seen: datetime | None = None
    logged_in_sites: list[str] = Field(default_factory=list)
    chrome_mode: str = "cdp"


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


class BroadcastNodeResult(BaseModel):
    node_id: str
    success: bool
    items: list[dict] = Field(default_factory=list)
    error: str | None = None


class BroadcastResult(BaseModel):
    total_nodes: int
    results: list[BroadcastNodeResult] = Field(default_factory=list)


class TaskStatusResult(BaseModel):
    task_id: str
    status: str
    items: list[dict] = Field(default_factory=list)
    total_items: int = 0
    error: str | None = None
