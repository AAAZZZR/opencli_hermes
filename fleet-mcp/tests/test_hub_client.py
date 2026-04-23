"""Tests for fleet_mcp.hub_client — httpx mocked via respx."""

from __future__ import annotations

import respx
from httpx import Response

from fleet_mcp.hub_client import dispatch, get_task, get_task_records, list_nodes

BASE = "http://localhost:8031/api/v1"


@respx.mock
async def test_list_nodes_empty():
    respx.get(f"{BASE}/nodes").mock(return_value=Response(200, json=[]))
    assert await list_nodes() == []


@respx.mock
async def test_list_nodes_returns_models():
    respx.get(f"{BASE}/nodes").mock(return_value=Response(200, json=[{
        "id": "uuid-1",
        "label": "alice-mbp",
        "status": "online",
        "mode": "bridge",
        "os": "darwin",
        "logged_in_sites": ["zhihu", "xiaohongshu"],
        "opencli_version": "1.7.7",
        "last_seen_at": "2026-04-23T10:15:00+00:00",
        "created_at": "2026-04-20T00:00:00+00:00",
    }]))
    nodes = await list_nodes()
    assert len(nodes) == 1
    n = nodes[0]
    assert n.label == "alice-mbp"
    assert n.status == "online"
    assert "zhihu" in n.logged_in_sites
    assert n.opencli_version == "1.7.7"


@respx.mock
async def test_dispatch_happy_path():
    respx.post(f"{BASE}/tasks").mock(return_value=Response(200, json={
        "id": "task-1", "node_id": "uuid-1",
        "site": "zhihu", "command": "hot",
        "args": {}, "positional_args": [], "format": "json", "timeout_sec": 120,
        "status": "completed",
        "items_total": 2, "items_stored": 2, "duration_ms": 4521,
        "created_at": "2026-04-23T10:15:00+00:00",
        "items": [{"title": "A"}, {"title": "B"}],
    }))
    task = await dispatch(node_id="alice-mbp", site="zhihu", command="hot")
    assert task.status == "completed"
    assert len(task.items) == 2


@respx.mock
async def test_dispatch_sends_expected_payload():
    route = respx.post(f"{BASE}/tasks").mock(return_value=Response(200, json={
        "id": "task-1", "node_id": "n",
        "site": "zhihu", "command": "hot",
        "args": {"limit": 5}, "positional_args": ["x"],
        "format": "json", "timeout_sec": 60,
        "status": "completed",
        "items_total": 0, "items_stored": 0, "duration_ms": 100,
        "created_at": "2026-04-23T10:15:00+00:00",
        "items": [],
    }))
    await dispatch(
        node_id="alice", site="zhihu", command="hot",
        args={"limit": 5}, positional_args=["x"], timeout=60,
    )
    assert route.called
    sent = route.calls[0].request
    body = sent.read()
    # Loose check — verify key fields appear in body
    assert b'"node_id":"alice"' in body
    assert b'"limit":5' in body
    assert b'"positional_args":["x"]' in body
    assert b'"timeout_sec":60' in body
    assert b'"wait":true' in body


@respx.mock
async def test_dispatch_failure_task_returned():
    respx.post(f"{BASE}/tasks").mock(return_value=Response(200, json={
        "id": "task-2", "node_id": "n", "site": "zhihu", "command": "hot",
        "args": {}, "positional_args": [], "format": "json", "timeout_sec": 120,
        "status": "failed",
        "error_code": "AUTH_REQUIRED", "error_message": "logged out",
        "exit_code": 77,
        "items_total": 0, "items_stored": 0, "duration_ms": 50,
        "created_at": "2026-04-23T10:15:00+00:00",
        "items": [],
    }))
    task = await dispatch(node_id="alice", site="zhihu", command="hot")
    assert task.status == "failed"
    assert task.error_code == "AUTH_REQUIRED"
    assert task.exit_code == 77


@respx.mock
async def test_get_task():
    respx.get(f"{BASE}/tasks/task-abc").mock(return_value=Response(200, json={
        "id": "task-abc", "node_id": "n", "site": "zhihu", "command": "hot",
        "args": {}, "positional_args": [], "format": "json", "timeout_sec": 120,
        "status": "completed",
        "items_total": 1, "items_stored": 1, "duration_ms": 200,
        "created_at": "2026-04-23T10:15:00+00:00",
    }))
    task = await get_task("task-abc")
    assert task.status == "completed"
    assert task.items == []  # no items on /tasks/{id}


@respx.mock
async def test_get_task_records():
    respx.get(f"{BASE}/tasks/task-abc/records").mock(return_value=Response(200, json={
        "items": [{"title": "x"}, {"title": "y"}],
        "total": 2,
    }))
    records = await get_task_records("task-abc")
    assert records.total == 2
    assert records.items[0]["title"] == "x"
