"""Pydantic schemas for REST and WS I/O."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class NodeCreate(BaseModel):
    label: str = Field(min_length=1, max_length=128)


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    mode: str | None = None
    os: str | None = None
    logged_in_sites: list[str] = Field(default_factory=list)
    opencli_version: str | None = None
    status: str = "offline"
    last_seen_at: datetime | None = None
    created_at: datetime


class NodeCreated(NodeOut):
    """NodeOut with the token — returned once at POST /nodes."""
    token: str


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

class TaskCreate(BaseModel):
    node_id: str = Field(description="Node id or label")
    site: str = Field(min_length=1, max_length=64)
    command: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    positional_args: list[Any] = Field(default_factory=list)
    format: str = "json"
    timeout_sec: int | None = Field(default=None, ge=1, le=600)
    wait: bool = True


class TaskError(BaseModel):
    code: str | None = None
    message: str | None = None
    exit_code: int | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    node_id: str
    site: str
    command: str
    args: dict[str, Any]
    positional_args: list[Any]
    format: str
    timeout_sec: int
    status: str
    error_code: str | None = None
    error_message: str | None = None
    exit_code: int | None = None
    items_total: int = 0
    items_stored: int = 0
    duration_ms: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskResult(TaskOut):
    """TaskOut extended with inline items — returned when wait=true or on request."""
    items: list[dict[str, Any]] = Field(default_factory=list)


class RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    content_hash: str
    raw_data: dict[str, Any]
    normalized_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RecordList(BaseModel):
    items: list[dict[str, Any]]
    total: int


# ---------------------------------------------------------------------------
# WS frames (what agent and hub send)
# ---------------------------------------------------------------------------

class WSRegister(BaseModel):
    type: Literal["register"]
    token: str
    mode: Literal["bridge", "cdp"] = "bridge"
    os: Literal["darwin", "linux", "win"] | None = None
    logged_in_sites: list[str] = Field(default_factory=list)
    opencli_version: str | None = None


class WSRegistered(BaseModel):
    type: Literal["registered"] = "registered"
    node_id: str
    label: str


class WSCollect(BaseModel):
    type: Literal["collect"] = "collect"
    task_id: str
    site: str
    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    positional_args: list[Any] = Field(default_factory=list)
    format: str = "json"
    timeout: int = 120


class WSProgress(BaseModel):
    type: Literal["progress"]
    task_id: str
    message: str


class WSResultError(BaseModel):
    code: str | None = None
    message: str | None = None
    exit_code: int | None = None
    stderr: str | None = None


class WSResult(BaseModel):
    type: Literal["result"]
    task_id: str
    success: bool
    items: list[dict[str, Any]] = Field(default_factory=list)
    error: WSResultError | None = None
    exit_code: int | None = None
    duration_ms: int | None = None


class WSPing(BaseModel):
    type: Literal["ping"] = "ping"


class WSPong(BaseModel):
    type: Literal["pong"] = "pong"
