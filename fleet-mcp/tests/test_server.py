"""Tests for MCP server tools using FastMCP in-memory client."""

import pytest
import respx
from fastmcp import Client
from httpx import Response

from fleet_mcp.admin_client import _source_cache
from fleet_mcp.server import mcp

BASE = "http://localhost:8031/api/v1"


@pytest.fixture(autouse=True)
def _clear_caches():
    _source_cache.clear()
    yield
    _source_cache.clear()


@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c


# ---------------------------------------------------------------------------
# list_supported_sites
# ---------------------------------------------------------------------------

async def test_list_supported_sites(client: Client):
    result = await client.call_tool("list_supported_sites", {})
    data = result.data
    assert "sites" in data
    sites = {s["site"] for s in data["sites"]}
    assert "xiaohongshu" in sites
    assert "zhihu" in sites
    # Each site should have commands and description
    for s in data["sites"]:
        assert len(s["commands"]) > 0
        assert len(s["description"]) > 0


# ---------------------------------------------------------------------------
# list_nodes
# ---------------------------------------------------------------------------

@respx.mock
async def test_list_nodes_empty(client: Client):
    respx.get(f"{BASE}/nodes").mock(
        return_value=Response(200, json={"success": True, "data": []})
    )
    result = await client.call_tool("list_nodes", {})
    assert result.data["nodes"] == []


@respx.mock
async def test_list_nodes_with_data(client: Client):
    respx.get(f"{BASE}/nodes").mock(
        return_value=Response(200, json={
            "success": True,
            "data": [{
                "id": "abc",
                "url": "http://192.168.1.1:19823",
                "label": "test-node",
                "status": "online",
                "mode": "cdp",
                "protocol": "ws",
                "node_type": "shell",
            }],
        })
    )
    result = await client.call_tool("list_nodes", {})
    nodes = result.data["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == "test-node"
    assert nodes[0]["online"] is True


# ---------------------------------------------------------------------------
# dispatch — whitelist rejection
# ---------------------------------------------------------------------------

async def test_dispatch_rejects_unknown_site(client: Client):
    result = await client.call_tool("dispatch", {
        "node_id": "test",
        "site": "unknown_site",
        "command": "search",
    })
    data = result.data
    assert data["success"] is False
    assert "not supported" in data["error"].lower()


async def test_dispatch_rejects_forbidden_command(client: Client):
    result = await client.call_tool("dispatch", {
        "node_id": "test",
        "site": "xiaohongshu",
        "command": "eval",
    })
    data = result.data
    assert data["success"] is False
    assert "forbidden" in data["error"].lower()


async def test_dispatch_rejects_unknown_command(client: Client):
    result = await client.call_tool("dispatch", {
        "node_id": "test",
        "site": "zhihu",
        "command": "delete",
    })
    data = result.data
    assert data["success"] is False
    assert "not allowed" in data["error"].lower()


# ---------------------------------------------------------------------------
# dispatch — success path
# ---------------------------------------------------------------------------

@respx.mock
async def test_dispatch_success(client: Client):
    # ensure_source
    respx.get(f"{BASE}/sources").mock(
        return_value=Response(200, json={
            "success": True,
            "data": [{
                "id": "src-z",
                "name": "fleet:zhihu:hot",
                "channel_type": "opencli",
                "channel_config": {"site": "zhihu", "command": "hot"},
                "enabled": True,
            }],
        })
    )
    # trigger
    respx.post(f"{BASE}/tasks/trigger").mock(
        return_value=Response(200, json={
            "success": True,
            "data": {"id": "t1", "source_id": "src-z", "status": "pending"},
        })
    )
    # poll -> completed
    respx.get(f"{BASE}/tasks/t1").mock(
        return_value=Response(200, json={
            "success": True,
            "data": {"id": "t1", "source_id": "src-z", "status": "completed"},
        })
    )
    # records
    respx.get(f"{BASE}/records").mock(
        return_value=Response(200, json={
            "success": True,
            "data": [
                {"id": "r1", "task_id": "t1", "raw_data": {"title": "hot topic 1"}, "status": "raw"},
                {"id": "r2", "task_id": "t1", "raw_data": {"title": "hot topic 2"}, "status": "raw"},
            ],
        })
    )

    result = await client.call_tool("dispatch", {
        "node_id": "test-node",
        "site": "zhihu",
        "command": "hot",
    })
    data = result.data
    assert data["success"] is True
    assert data["node_id"] == "test-node"
    assert data["task_id"] == "t1"
    assert len(data["items"]) == 2


# ---------------------------------------------------------------------------
# dispatch_best — no eligible node
# ---------------------------------------------------------------------------

@respx.mock
async def test_dispatch_best_no_nodes(client: Client):
    respx.get(f"{BASE}/nodes").mock(
        return_value=Response(200, json={"success": True, "data": []})
    )
    result = await client.call_tool("dispatch_best", {
        "site": "zhihu",
        "command": "hot",
    })
    data = result.data
    assert data["success"] is False
    assert "no nodes" in data["error"].lower()


# ---------------------------------------------------------------------------
# get_task_status
# ---------------------------------------------------------------------------

@respx.mock
async def test_get_task_status(client: Client):
    respx.get(f"{BASE}/tasks/t99").mock(
        return_value=Response(200, json={
            "success": True,
            "data": {"id": "t99", "source_id": "src-1", "status": "completed"},
        })
    )
    respx.get(f"{BASE}/records").mock(
        return_value=Response(200, json={
            "success": True,
            "data": [
                {"id": "r1", "task_id": "t99", "raw_data": {"title": "full data"}, "status": "raw"},
            ],
        })
    )
    result = await client.call_tool("get_task_status", {"task_id": "t99"})
    data = result.data
    assert data["status"] == "completed"
    assert data["total_items"] == 1
    assert data["items"][0]["title"] == "full data"


# ---------------------------------------------------------------------------
# Output sanitization in dispatch
# ---------------------------------------------------------------------------

@respx.mock
async def test_dispatch_sanitizes_output(client: Client):
    respx.get(f"{BASE}/sources").mock(
        return_value=Response(200, json={
            "success": True,
            "data": [{
                "id": "src-x",
                "name": "fleet:xiaohongshu:search",
                "channel_type": "opencli",
                "channel_config": {"site": "xiaohongshu", "command": "search"},
                "enabled": True,
            }],
        })
    )
    respx.post(f"{BASE}/tasks/trigger").mock(
        return_value=Response(200, json={
            "success": True,
            "data": {"id": "t2", "source_id": "src-x", "status": "pending"},
        })
    )
    respx.get(f"{BASE}/tasks/t2").mock(
        return_value=Response(200, json={
            "success": True,
            "data": {"id": "t2", "source_id": "src-x", "status": "completed"},
        })
    )
    respx.get(f"{BASE}/records").mock(
        return_value=Response(200, json={
            "success": True,
            "data": [{
                "id": "r1",
                "task_id": "t2",
                "raw_data": {
                    "title": "post",
                    "cookie": "should-be-stripped",
                    "session_token": "secret",
                },
                "status": "raw",
            }],
        })
    )

    result = await client.call_tool("dispatch", {
        "node_id": "n1",
        "site": "xiaohongshu",
        "command": "search",
        "args": {"q": "test"},
    })
    data = result.data
    assert data["success"] is True
    item = data["items"][0]
    assert item["title"] == "post"
    assert "cookie" not in item
    assert "session_token" not in item
