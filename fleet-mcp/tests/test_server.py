"""Tests for MCP server tools using FastMCP in-memory client.

hub_client is patched so tests don't require a running fleet-hub.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest
from fastmcp import Client

from fleet_mcp import hub_client, server
from fleet_mcp.schemas import HubNode, HubRecordList, HubTaskResult
from fleet_mcp.security import RateLimiter


def _make_node(
    label: str,
    *,
    online: bool = True,
    sites: list[str] | None = None,
    last_seen_iso: str = "2026-04-23T10:00:00+00:00",
) -> HubNode:
    return HubNode.model_validate({
        "id": f"id-{label}",
        "label": label,
        "status": "online" if online else "offline",
        "mode": "bridge",
        "os": "darwin",
        "logged_in_sites": sites or [],
        "opencli_version": "1.7.7",
        "last_seen_at": last_seen_iso,
        "created_at": "2026-04-20T00:00:00+00:00",
    })


def _make_task(
    *,
    status: str = "completed",
    node_id: str = "alice",
    site: str = "zhihu",
    command: str = "hot",
    items: list[dict] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    exit_code: int | None = None,
) -> HubTaskResult:
    return HubTaskResult.model_validate({
        "id": "task-x",
        "node_id": node_id,
        "site": site,
        "command": command,
        "args": {}, "positional_args": [],
        "format": "json", "timeout_sec": 120,
        "status": status,
        "items_total": len(items or []),
        "items_stored": len(items or []),
        "duration_ms": 100,
        "items": items or [],
        "error_code": error_code,
        "error_message": error_message,
        "exit_code": exit_code,
        "created_at": "2026-04-23T10:00:00+00:00",
    })


@pytest.fixture(autouse=True)
def _reset_rate_limiter(monkeypatch):
    # Fresh buckets per test so rate limits don't leak across tests.
    monkeypatch.setattr(
        "fleet_mcp.server.rate_limiter",
        RateLimiter(per_node_rpm=6000, global_rpm=6000),
    )


@pytest.fixture
async def client():
    async with Client(server.mcp) as c:
        yield c


# ---------------------------------------------------------------------------
# list_supported_sites
# ---------------------------------------------------------------------------

async def test_list_supported_sites(client: Client):
    result = await client.call_tool("list_supported_sites", {})
    data = result.data
    assert "sites" in data
    sites_by_name = {s["site"]: s for s in data["sites"]}
    # Core sites stay in
    for name in ("xiaohongshu", "zhihu", "reddit", "twitter"):
        assert name in sites_by_name
    # Expanded catalogue includes dozens more
    assert len(sites_by_name) >= 100
    # Every site has a description + both command lists
    for s in data["sites"]:
        assert isinstance(s["allowed_commands"], list)
        assert isinstance(s["blocked_commands"], list)
        assert len(s["description"]) > 0
    # Sites with known writes expose them via blocked_commands
    assert "answer" in sites_by_name["zhihu"]["blocked_commands"]
    assert "comment" in sites_by_name["reddit"]["blocked_commands"]
    assert "post" in sites_by_name["twitter"]["blocked_commands"]
    # Read-heavy sites have no blocked_commands
    assert sites_by_name["arxiv"]["blocked_commands"] == []
    assert sites_by_name["wikipedia"]["blocked_commands"] == []
    # allowed_commands gives the LLM concrete command names to use.
    # Without this, Hermes guessed `web fetch` on 2026-04-24 (doesn't exist;
    # only `web read` does); `reddit read` was similarly unreachable.
    assert "read" in sites_by_name["reddit"]["allowed_commands"]
    assert "search" in sites_by_name["reddit"]["allowed_commands"]
    assert sites_by_name["web"]["allowed_commands"] == ["read"]
    assert "answer" not in sites_by_name["zhihu"]["allowed_commands"]  # blocked
    # Pure-write sites have empty allowed_commands
    assert sites_by_name["grok"]["allowed_commands"] == []


# ---------------------------------------------------------------------------
# list_nodes
# ---------------------------------------------------------------------------

async def test_list_nodes(client: Client, monkeypatch):
    async def _fake() -> list[HubNode]:
        return [_make_node("alice", online=True, sites=["zhihu", "xiaohongshu"])]

    monkeypatch.setattr(hub_client, "list_nodes", _fake)
    result = await client.call_tool("list_nodes", {})
    nodes = result.data["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == "alice"
    assert nodes[0]["online"] is True
    assert "zhihu" in nodes[0]["logged_in_sites"]
    assert nodes[0]["opencli_version"] == "1.7.7"


# ---------------------------------------------------------------------------
# dispatch — whitelist
# ---------------------------------------------------------------------------

async def test_dispatch_rejects_unknown_site(client: Client):
    result = await client.call_tool("dispatch", {
        "node_id": "alice", "site": "unknown", "command": "search",
    })
    data = result.data
    assert data["success"] is False
    assert "not supported" in data["error"].lower()


async def test_dispatch_rejects_forbidden_command(client: Client):
    result = await client.call_tool("dispatch", {
        "node_id": "alice", "site": "xiaohongshu", "command": "eval",
    })
    data = result.data
    assert data["success"] is False
    assert "forbidden" in data["error"].lower()


async def test_dispatch_rejects_new_forbidden_commands(client: Client):
    # install/plugin are newly added to the forbidden list
    for cmd in ("install", "plugin", "daemon", "record"):
        result = await client.call_tool("dispatch", {
            "node_id": "alice", "site": "zhihu", "command": cmd,
        })
        data = result.data
        assert data["success"] is False
        assert "forbidden" in data["error"].lower(), f"{cmd} should be forbidden"


# ---------------------------------------------------------------------------
# dispatch — success & failure paths
# ---------------------------------------------------------------------------

async def test_dispatch_success(client: Client, monkeypatch):
    async def _fake(**kwargs: Any) -> HubTaskResult:
        return _make_task(items=[{"title": "hot #1"}, {"title": "hot #2"}])

    monkeypatch.setattr(hub_client, "dispatch", _fake)

    result = await client.call_tool("dispatch", {
        "node_id": "alice", "site": "zhihu", "command": "hot",
    })
    data = result.data
    assert data["success"] is True
    assert data["task_id"] == "task-x"
    assert len(data["items"]) == 2


async def test_dispatch_failure_propagates_error_code(client: Client, monkeypatch):
    async def _fake(**kwargs: Any) -> HubTaskResult:
        return _make_task(
            status="failed", items=[],
            error_code="AUTH_REQUIRED", error_message="logged out",
            exit_code=77,
        )

    monkeypatch.setattr(hub_client, "dispatch", _fake)

    result = await client.call_tool("dispatch", {
        "node_id": "alice", "site": "zhihu", "command": "hot",
    })
    data = result.data
    assert data["success"] is False
    assert data["error_code"] == "AUTH_REQUIRED"
    assert data["exit_code"] == 77


async def test_dispatch_hub_exception(client: Client, monkeypatch):
    async def _fake(**kwargs: Any) -> HubTaskResult:
        raise RuntimeError("hub unreachable")

    monkeypatch.setattr(hub_client, "dispatch", _fake)

    result = await client.call_tool("dispatch", {
        "node_id": "alice", "site": "zhihu", "command": "hot",
    })
    data = result.data
    assert data["success"] is False
    assert "hub error" in data["error"]


async def test_dispatch_sanitizes_items(client: Client, monkeypatch):
    async def _fake(**kwargs: Any) -> HubTaskResult:
        return _make_task(items=[{"title": "ok", "cookie": "x", "session_token": "y"}])

    monkeypatch.setattr(hub_client, "dispatch", _fake)

    result = await client.call_tool("dispatch", {
        "node_id": "alice", "site": "xiaohongshu", "command": "search",
    })
    item = result.data["items"][0]
    assert item["title"] == "ok"
    assert "cookie" not in item
    assert "session_token" not in item


async def test_dispatch_truncates_large_results(client: Client, monkeypatch):
    async def _fake(**kwargs: Any) -> HubTaskResult:
        return _make_task(items=[{"id": i} for i in range(200)])

    monkeypatch.setattr(hub_client, "dispatch", _fake)

    result = await client.call_tool("dispatch", {
        "node_id": "alice", "site": "zhihu", "command": "hot",
    })
    data = result.data
    assert data["success"] is True
    assert data["truncated"] is True
    assert len(data["items"]) == 50  # MAX_ITEMS_INLINE default


# ---------------------------------------------------------------------------
# dispatch_best
# ---------------------------------------------------------------------------

async def test_dispatch_best_no_nodes(client: Client, monkeypatch):
    async def _fake_list():
        return []

    monkeypatch.setattr(hub_client, "list_nodes", _fake_list)

    result = await client.call_tool("dispatch_best", {
        "site": "zhihu", "command": "hot",
    })
    data = result.data
    assert data["success"] is False
    assert "no nodes" in data["error"].lower()


async def test_dispatch_best_falls_back_when_no_logged_in_node(client: Client, monkeypatch):
    """If no node reports being logged into the target site, dispatch_best
    falls back to any online node. Many sites (arxiv, wikipedia, ...) don't
    need login; for sites that do, AUTH_REQUIRED propagates back per-task.
    """
    captured: dict[str, Any] = {}

    async def _fake_list():
        return [_make_node("alice", sites=["xiaohongshu"])]  # not logged into zhihu

    async def _fake_dispatch(**kwargs: Any) -> HubTaskResult:
        captured["node_id"] = kwargs["node_id"]
        return _make_task(node_id=kwargs["node_id"], items=[{"title": "t"}])

    monkeypatch.setattr(hub_client, "list_nodes", _fake_list)
    monkeypatch.setattr(hub_client, "dispatch", _fake_dispatch)

    result = await client.call_tool("dispatch_best", {
        "site": "zhihu", "command": "hot",
    })
    data = result.data
    assert data["success"] is True
    assert captured["node_id"] == "alice"  # fell back to the online non-logged-in node


async def test_dispatch_best_picks_lru(client: Client, monkeypatch):
    captured: dict[str, Any] = {}

    async def _fake_list():
        return [
            _make_node("newer", sites=["zhihu"], last_seen_iso="2026-04-23T10:00:00+00:00"),
            _make_node("older", sites=["zhihu"], last_seen_iso="2026-04-23T09:00:00+00:00"),
        ]

    async def _fake_dispatch(**kwargs: Any) -> HubTaskResult:
        captured["node_id"] = kwargs["node_id"]
        return _make_task(node_id=kwargs["node_id"], items=[])

    monkeypatch.setattr(hub_client, "list_nodes", _fake_list)
    monkeypatch.setattr(hub_client, "dispatch", _fake_dispatch)

    result = await client.call_tool("dispatch_best", {
        "site": "zhihu", "command": "hot",
    })
    assert result.data["success"] is True
    assert captured["node_id"] == "older"


async def test_dispatch_best_handles_none_last_seen(client: Client, monkeypatch):
    """Regression: `last_seen_at: None` + `datetime` sentinel mix must not crash."""

    async def _fake_list():
        # One node has never been seen
        n1 = _make_node("fresh", sites=["zhihu"])
        n1.last_seen_at = None
        n2 = _make_node("older", sites=["zhihu"], last_seen_iso="2026-04-23T09:00:00+00:00")
        return [n1, n2]

    async def _fake_dispatch(**kwargs: Any) -> HubTaskResult:
        return _make_task(node_id=kwargs["node_id"])

    monkeypatch.setattr(hub_client, "list_nodes", _fake_list)
    monkeypatch.setattr(hub_client, "dispatch", _fake_dispatch)

    result = await client.call_tool("dispatch_best", {
        "site": "zhihu", "command": "hot",
    })
    # Should not raise; None last_seen sorts earlier than any datetime.
    assert result.data["success"] is True


# ---------------------------------------------------------------------------
# broadcast
# ---------------------------------------------------------------------------

async def test_broadcast_fans_out(client: Client, monkeypatch):
    async def _fake_list():
        return [
            _make_node("alice", sites=["zhihu"]),
            _make_node("bob", sites=["zhihu"]),
        ]

    async def _fake_dispatch(**kwargs: Any) -> HubTaskResult:
        return _make_task(node_id=kwargs["node_id"], items=[{"nid": kwargs["node_id"]}])

    monkeypatch.setattr(hub_client, "list_nodes", _fake_list)
    monkeypatch.setattr(hub_client, "dispatch", _fake_dispatch)

    result = await client.call_tool("broadcast", {
        "site": "zhihu", "command": "hot",
    })
    data = result.data
    assert data["total_nodes"] == 2
    assert all(r["success"] for r in data["results"])


async def test_broadcast_no_online_nodes(client: Client, monkeypatch):
    async def _fake_list():
        return [_make_node("alice", online=False, sites=["zhihu"])]

    monkeypatch.setattr(hub_client, "list_nodes", _fake_list)
    result = await client.call_tool("broadcast", {
        "site": "zhihu", "command": "hot",
    })
    assert result.data["total_nodes"] == 0


async def test_broadcast_partial_failure(client: Client, monkeypatch):
    async def _fake_list():
        return [
            _make_node("alice", sites=["zhihu"]),
            _make_node("bob", sites=["zhihu"]),
        ]

    async def _fake_dispatch(**kwargs: Any) -> HubTaskResult:
        if kwargs["node_id"] == "alice":
            return _make_task(node_id="alice", items=[{"x": 1}])
        return _make_task(
            node_id="bob", status="failed",
            error_code="AUTH_REQUIRED", error_message="logged out",
        )

    monkeypatch.setattr(hub_client, "list_nodes", _fake_list)
    monkeypatch.setattr(hub_client, "dispatch", _fake_dispatch)

    result = await client.call_tool("broadcast", {
        "site": "zhihu", "command": "hot",
    })
    data = result.data
    results = {r["node_id"]: r for r in data["results"]}
    assert results["alice"]["success"] is True
    assert results["bob"]["success"] is False
    assert results["bob"]["error_code"] == "AUTH_REQUIRED"


# ---------------------------------------------------------------------------
# get_task_status
# ---------------------------------------------------------------------------

async def test_get_task_status_completed(client: Client, monkeypatch):
    async def _fake_get(task_id: str) -> HubTaskResult:
        return _make_task(items=[])

    async def _fake_records(task_id: str, limit: int = 500) -> HubRecordList:
        return HubRecordList(
            items=[{"title": "full x"}, {"title": "full y"}], total=2,
        )

    monkeypatch.setattr(hub_client, "get_task", _fake_get)
    monkeypatch.setattr(hub_client, "get_task_records", _fake_records)

    result = await client.call_tool("get_task_status", {"task_id": "task-x"})
    data = result.data
    assert data["status"] == "completed"
    assert data["total_items"] == 2


async def test_get_task_status_failed(client: Client, monkeypatch):
    async def _fake_get(task_id: str) -> HubTaskResult:
        return _make_task(
            status="failed", items=[],
            error_code="TIMEOUT", error_message="agent silent",
        )

    monkeypatch.setattr(hub_client, "get_task", _fake_get)

    result = await client.call_tool("get_task_status", {"task_id": "task-x"})
    data = result.data
    assert data["status"] == "failed"
    assert data["error_code"] == "TIMEOUT"
