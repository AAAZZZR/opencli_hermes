"""Async HTTP client wrapping the opencli-admin REST API.

opencli-admin uses a Source -> Task -> Record model:
  1. A DataSource defines what to collect (site + command).
  2. POST /tasks/trigger creates a task referencing a source_id.
  3. opencli-admin picks a node, dispatches via WS, stores results as Records.

This client hides that complexity behind simple methods that fleet-mcp tools call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from fleet_mcp.config import settings
from fleet_mcp.schemas import (
    AdminNode,
    AdminRecord,
    AdminSource,
    AdminTask,
    AdminTaskRun,
)

logger = logging.getLogger(__name__)

_BASE: str = settings.admin_api_url.rstrip("/") + "/api/v1"

# In-memory cache: (site, command) -> source_id
_source_cache: dict[tuple[str, str], str] = {}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_BASE, timeout=30.0)


def _unwrap(resp: httpx.Response) -> Any:
    """Raise on HTTP error, then return the 'data' field from ApiResponse."""
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success", True):
        raise RuntimeError(body.get("error", "unknown admin API error"))
    return body.get("data")


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def list_nodes() -> list[AdminNode]:
    async with _client() as c:
        data = _unwrap(await c.get("/nodes"))
    if not data:
        return []
    return [AdminNode.model_validate(n) for n in data]


# ---------------------------------------------------------------------------
# Sources — lazy create-if-not-exists for each (site, command)
# ---------------------------------------------------------------------------

async def _list_sources() -> list[AdminSource]:
    async with _client() as c:
        data = _unwrap(await c.get("/sources"))
    if not data:
        return []
    return [AdminSource.model_validate(s) for s in data]


async def _create_source(site: str, command: str) -> AdminSource:
    payload = {
        "name": f"fleet:{site}:{command}",
        "channel_type": "opencli",
        "channel_config": {"site": site, "command": command},
        "enabled": True,
    }
    async with _client() as c:
        data = _unwrap(await c.post("/sources", json=payload))
    return AdminSource.model_validate(data)


async def ensure_source(site: str, command: str) -> str:
    """Return the source_id for a (site, command) pair, creating if needed."""
    key = (site, command)
    if key in _source_cache:
        return _source_cache[key]

    # Check existing sources
    for src in await _list_sources():
        cfg = src.channel_config
        if cfg.get("site") == site and cfg.get("command") == command:
            _source_cache[key] = src.id
            return src.id

    # Create new
    src = await _create_source(site, command)
    _source_cache[key] = src.id
    logger.info("Created source %s for (%s, %s)", src.id, site, command)
    return src.id


# ---------------------------------------------------------------------------
# Tasks — trigger + poll until done
# ---------------------------------------------------------------------------

async def trigger_task(
    source_id: str,
    parameters: dict[str, Any] | None = None,
    priority: int = 5,
) -> AdminTask:
    payload: dict[str, Any] = {"source_id": source_id, "priority": priority}
    if parameters:
        payload["parameters"] = parameters
    async with _client() as c:
        data = _unwrap(await c.post("/tasks/trigger", json=payload))
    return AdminTask.model_validate(data)


async def get_task(task_id: str) -> AdminTask:
    async with _client() as c:
        data = _unwrap(await c.get(f"/tasks/{task_id}"))
    return AdminTask.model_validate(data)


async def get_task_runs(task_id: str) -> list[AdminTaskRun]:
    async with _client() as c:
        data = _unwrap(await c.get(f"/tasks/{task_id}/runs"))
    if not data:
        return []
    return [AdminTaskRun.model_validate(r) for r in data]


async def poll_task(
    task_id: str,
    timeout: float | None = None,
    poll_interval: float | None = None,
) -> AdminTask:
    """Poll until task reaches a terminal status or timeout."""
    _timeout = timeout or settings.task_timeout_sec
    _interval = poll_interval or settings.task_poll_interval_sec
    terminal = {"completed", "failed", "cancelled"}
    elapsed = 0.0

    while elapsed < _timeout:
        task = await get_task(task_id)
        if task.status in terminal:
            return task
        await asyncio.sleep(_interval)
        elapsed += _interval

    raise TimeoutError(f"Task {task_id} did not finish within {_timeout}s")


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

async def get_records(task_id: str) -> list[AdminRecord]:
    async with _client() as c:
        resp = await c.get("/records", params={"task_id": task_id, "limit": 500})
        data = _unwrap(resp)
    if not data:
        return []
    return [AdminRecord.model_validate(r) for r in data]


# ---------------------------------------------------------------------------
# High-level: dispatch and wait
# ---------------------------------------------------------------------------

async def dispatch_and_wait(
    site: str,
    command: str,
    args: dict[str, Any] | None = None,
    node_id: str | None = None,
    timeout: float | None = None,
) -> tuple[AdminTask, list[AdminRecord]]:
    """End-to-end: ensure source, trigger, poll, fetch records.

    Returns (task, records) tuple.
    """
    source_id = await ensure_source(site, command)

    params: dict[str, Any] = {}
    if args:
        params.update(args)
    if node_id:
        params["node_id"] = node_id

    task = await trigger_task(source_id, parameters=params or None)
    task = await poll_task(task.id, timeout=timeout)

    records: list[AdminRecord] = []
    if task.status == "completed":
        records = await get_records(task.id)

    return task, records
