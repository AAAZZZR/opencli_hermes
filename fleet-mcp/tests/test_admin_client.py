"""Tests for admin_client — mock httpx, verify correct URL construction."""

import pytest
import respx
from httpx import Response

from fleet_mcp.admin_client import (
    _source_cache,
    dispatch_and_wait,
    ensure_source,
    get_records,
    get_task,
    list_nodes,
    trigger_task,
)

BASE = "http://localhost:8031/api/v1"


@pytest.fixture(autouse=True)
def _clear_source_cache():
    _source_cache.clear()
    yield
    _source_cache.clear()


# ---------------------------------------------------------------------------
# list_nodes
# ---------------------------------------------------------------------------

@respx.mock
async def test_list_nodes_empty():
    respx.get(f"{BASE}/nodes").mock(
        return_value=Response(200, json={"success": True, "data": []})
    )
    nodes = await list_nodes()
    assert nodes == []


@respx.mock
async def test_list_nodes_returns_models():
    respx.get(f"{BASE}/nodes").mock(
        return_value=Response(200, json={
            "success": True,
            "data": [{
                "id": "abc-123",
                "url": "http://192.168.1.100:19823",
                "label": "alice-mbp",
                "status": "online",
                "mode": "cdp",
                "protocol": "ws",
                "node_type": "shell",
            }],
        })
    )
    nodes = await list_nodes()
    assert len(nodes) == 1
    assert nodes[0].id == "abc-123"
    assert nodes[0].label == "alice-mbp"
    assert nodes[0].status == "online"


# ---------------------------------------------------------------------------
# ensure_source
# ---------------------------------------------------------------------------

@respx.mock
async def test_ensure_source_creates_when_missing():
    respx.get(f"{BASE}/sources").mock(
        return_value=Response(200, json={"success": True, "data": []})
    )
    respx.post(f"{BASE}/sources").mock(
        return_value=Response(200, json={
            "success": True,
            "data": {
                "id": "src-001",
                "name": "fleet:zhihu:hot",
                "channel_type": "opencli",
                "channel_config": {"site": "zhihu", "command": "hot"},
                "enabled": True,
            },
        })
    )
    source_id = await ensure_source("zhihu", "hot")
    assert source_id == "src-001"
    # Second call should use cache
    source_id2 = await ensure_source("zhihu", "hot")
    assert source_id2 == "src-001"


@respx.mock
async def test_ensure_source_finds_existing():
    respx.get(f"{BASE}/sources").mock(
        return_value=Response(200, json={
            "success": True,
            "data": [{
                "id": "src-existing",
                "name": "fleet:zhihu:hot",
                "channel_type": "opencli",
                "channel_config": {"site": "zhihu", "command": "hot"},
                "enabled": True,
            }],
        })
    )
    source_id = await ensure_source("zhihu", "hot")
    assert source_id == "src-existing"


# ---------------------------------------------------------------------------
# trigger_task
# ---------------------------------------------------------------------------

@respx.mock
async def test_trigger_task():
    respx.post(f"{BASE}/tasks/trigger").mock(
        return_value=Response(200, json={
            "success": True,
            "data": {
                "id": "task-001",
                "source_id": "src-001",
                "status": "pending",
            },
        })
    )
    task = await trigger_task("src-001", parameters={"q": "AI"})
    assert task.id == "task-001"
    assert task.status == "pending"


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------

@respx.mock
async def test_get_task():
    respx.get(f"{BASE}/tasks/task-001").mock(
        return_value=Response(200, json={
            "success": True,
            "data": {
                "id": "task-001",
                "source_id": "src-001",
                "status": "completed",
            },
        })
    )
    task = await get_task("task-001")
    assert task.status == "completed"


# ---------------------------------------------------------------------------
# get_records
# ---------------------------------------------------------------------------

@respx.mock
async def test_get_records():
    respx.get(f"{BASE}/records").mock(
        return_value=Response(200, json={
            "success": True,
            "data": [
                {
                    "id": "rec-1",
                    "task_id": "task-001",
                    "raw_data": {"title": "hello"},
                    "status": "raw",
                },
            ],
        })
    )
    records = await get_records("task-001")
    assert len(records) == 1
    assert records[0].raw_data == {"title": "hello"}


# ---------------------------------------------------------------------------
# dispatch_and_wait
# ---------------------------------------------------------------------------

@respx.mock
async def test_dispatch_and_wait_success():
    respx.get(f"{BASE}/sources").mock(
        return_value=Response(200, json={
            "success": True,
            "data": [{
                "id": "src-001",
                "name": "fleet:zhihu:hot",
                "channel_type": "opencli",
                "channel_config": {"site": "zhihu", "command": "hot"},
                "enabled": True,
            }],
        })
    )
    respx.post(f"{BASE}/tasks/trigger").mock(
        return_value=Response(200, json={
            "success": True,
            "data": {"id": "task-002", "source_id": "src-001", "status": "completed"},
        })
    )
    # poll_task calls get_task — return completed immediately
    respx.get(f"{BASE}/tasks/task-002").mock(
        return_value=Response(200, json={
            "success": True,
            "data": {"id": "task-002", "source_id": "src-001", "status": "completed"},
        })
    )
    respx.get(f"{BASE}/records").mock(
        return_value=Response(200, json={
            "success": True,
            "data": [
                {"id": "r1", "task_id": "task-002", "raw_data": {"title": "item1"}, "status": "raw"},
            ],
        })
    )
    task, records = await dispatch_and_wait("zhihu", "hot")
    assert task.status == "completed"
    assert len(records) == 1
