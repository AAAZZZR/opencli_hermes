"""Async HTTP client for fleet-hub.

The hub exposes:
  GET    /api/v1/nodes
  POST   /api/v1/tasks         → dispatch + (optionally) wait + return TaskResult
  GET    /api/v1/tasks/{id}
  GET    /api/v1/tasks/{id}/records

When dispatching with `wait=true` (our default), the hub synchronously waits
for the agent's result frame, stores records, and returns the full task
(including `items`). fleet-mcp therefore doesn't need any polling logic.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from fleet_mcp.config import settings
from fleet_mcp.schemas import HubNode, HubRecordList, HubTaskResult

logger = logging.getLogger(__name__)


def _base() -> str:
    return settings.hub_url.rstrip("/") + "/api/v1"


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_base(), timeout=timeout)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def list_nodes() -> list[HubNode]:
    async with _client() as c:
        r = await c.get("/nodes")
    r.raise_for_status()
    return [HubNode.model_validate(n) for n in r.json()]


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

async def dispatch(
    *,
    node_id: str,
    site: str,
    command: str,
    args: dict[str, Any] | None = None,
    positional_args: list[Any] | None = None,
    format: str = "json",
    timeout: int | None = None,
    wait: bool = True,
) -> HubTaskResult:
    """Ask the hub to dispatch one collect task to a node.

    `node_id` may be the node's UUID or its user-friendly label — the hub
    accepts either.
    """
    payload: dict[str, Any] = {
        "node_id": node_id,
        "site": site,
        "command": command,
        "args": args or {},
        "positional_args": positional_args or [],
        "format": format,
        "wait": wait,
    }
    if timeout is not None:
        payload["timeout_sec"] = timeout

    # Client timeout must exceed the task timeout so we don't give up before
    # the hub does.
    http_timeout = (timeout or settings.task_timeout_sec) + 30
    async with _client(timeout=http_timeout) as c:
        r = await c.post("/tasks", json=payload)
    r.raise_for_status()
    return HubTaskResult.model_validate(r.json())


async def get_task(task_id: str) -> HubTaskResult:
    async with _client() as c:
        r = await c.get(f"/tasks/{task_id}")
    r.raise_for_status()
    data = r.json()
    # /tasks/{id} has no items; add an empty list for model compatibility.
    data.setdefault("items", [])
    return HubTaskResult.model_validate(data)


async def get_task_records(task_id: str, limit: int = 500) -> HubRecordList:
    async with _client() as c:
        r = await c.get(f"/tasks/{task_id}/records", params={"limit": limit})
    r.raise_for_status()
    return HubRecordList.model_validate(r.json())
