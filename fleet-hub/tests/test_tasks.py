"""REST tests for /api/v1/tasks — dispatch path is stubbed via WSManager patch."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

import fleet_hub.ws.manager as ws_manager_module
from fleet_hub.ws.manager import NodeOffline


class _StubManager:
    """Replaces the real WSManager for dispatch-level tests.

    Configure with `set_result(...)` to control what `dispatch` returns.
    """

    def __init__(self) -> None:
        self._online: set[str] = set()
        self._result: dict[str, Any] | None = None
        self._exception: Exception | None = None

    def mark_online(self, node_id: str) -> None:
        self._online.add(node_id)

    def set_result(self, frame: dict[str, Any]) -> None:
        self._result = frame
        self._exception = None

    def set_exception(self, exc: Exception) -> None:
        self._exception = exc
        self._result = None

    def is_online(self, node_id: str) -> bool:
        return node_id in self._online

    def online_node_ids(self) -> set[str]:
        return set(self._online)

    async def dispatch(self, node_id: str, frame: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        if self._exception is not None:
            raise self._exception
        if node_id not in self._online:
            raise NodeOffline(f"{node_id} offline (stub)")
        assert self._result is not None, "stub result not configured"
        return {**self._result, "task_id": frame["task_id"]}

    async def detach(self, node_id: str) -> None:
        self._online.discard(node_id)

    async def attach(self, *_a, **_kw):  # pragma: no cover — not used in these tests
        raise NotImplementedError

    def resolve(self, *_a, **_kw):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def stub_manager(monkeypatch):
    stub = _StubManager()
    # Patch the submodule attribute + wherever it's already been imported into routers.
    monkeypatch.setattr(ws_manager_module, "manager", stub)
    monkeypatch.setattr("fleet_hub.api.nodes.manager", stub)
    monkeypatch.setattr("fleet_hub.api.tasks.manager", stub)
    return stub


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

async def test_dispatch_success_stores_records(client: AsyncClient, stub_manager: _StubManager) -> None:
    node = (await client.post("/api/v1/nodes", json={"label": "alice"})).json()
    stub_manager.mark_online(node["id"])
    stub_manager.set_result({
        "type": "result",
        "success": True,
        "items": [
            {"id": "1", "title": "Zhihu hot #1", "url": "https://z.h/1"},
            {"id": "2", "title": "Zhihu hot #2", "url": "https://z.h/2"},
        ],
        "exit_code": 0,
        "duration_ms": 4500,
    })

    resp = await client.post("/api/v1/tasks", json={
        "node_id": "alice",
        "site": "zhihu",
        "command": "hot",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["items_total"] == 2
    assert body["items_stored"] == 2
    assert len(body["items"]) == 2


async def test_dispatch_dedups_across_records(client: AsyncClient, stub_manager: _StubManager) -> None:
    node = (await client.post("/api/v1/nodes", json={"label": "alice"})).json()
    stub_manager.mark_online(node["id"])
    stub_manager.set_result({
        "type": "result",
        "success": True,
        "items": [
            {"id": "1", "title": "A", "url": "https://z.h/1"},
            {"id": "1", "title": "A", "url": "https://z.h/1"},  # duplicate
            {"id": "2", "title": "B", "url": "https://z.h/2"},
        ],
        "exit_code": 0,
    })
    resp = await client.post("/api/v1/tasks", json={
        "node_id": "alice", "site": "zhihu", "command": "hot",
    })
    body = resp.json()
    assert body["items_total"] == 3
    assert body["items_stored"] == 2


async def test_dispatch_sanitizes_items(client: AsyncClient, stub_manager: _StubManager) -> None:
    node = (await client.post("/api/v1/nodes", json={"label": "alice"})).json()
    stub_manager.mark_online(node["id"])
    stub_manager.set_result({
        "type": "result",
        "success": True,
        "items": [
            {"id": "1", "title": "post", "cookie": "xxx", "session_token": "yyy"},
        ],
        "exit_code": 0,
    })
    resp = await client.post("/api/v1/tasks", json={
        "node_id": "alice", "site": "xiaohongshu", "command": "search",
    })
    body = resp.json()
    item = body["items"][0]
    assert item["title"] == "post"
    # Check raw record too
    records = (await client.get(f"/api/v1/tasks/{body['id']}/records")).json()
    stored = records["items"][0]
    assert "cookie" not in stored
    assert "session_token" not in stored


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

async def test_dispatch_unknown_node_404(client: AsyncClient, stub_manager: _StubManager) -> None:
    resp = await client.post("/api/v1/tasks", json={
        "node_id": "ghost", "site": "zhihu", "command": "hot",
    })
    assert resp.status_code == 404


async def test_dispatch_node_offline(client: AsyncClient, stub_manager: _StubManager) -> None:
    await client.post("/api/v1/nodes", json={"label": "alice"})
    # Note: stub_manager.mark_online NOT called → dispatch raises NodeOffline
    resp = await client.post("/api/v1/tasks", json={
        "node_id": "alice", "site": "zhihu", "command": "hot",
    })
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "NODE_OFFLINE"


async def test_dispatch_agent_returns_auth_required(client: AsyncClient, stub_manager: _StubManager) -> None:
    node = (await client.post("/api/v1/nodes", json={"label": "alice"})).json()
    stub_manager.mark_online(node["id"])
    stub_manager.set_result({
        "type": "result",
        "success": False,
        "items": [],
        "error": {
            "code": "AUTH_REQUIRED",
            "message": "Zhihu logged out",
            "exit_code": 77,
        },
    })
    resp = await client.post("/api/v1/tasks", json={
        "node_id": "alice", "site": "zhihu", "command": "hot",
    })
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "AUTH_REQUIRED"
    assert body["exit_code"] == 77


async def test_dispatch_timeout(client: AsyncClient, stub_manager: _StubManager) -> None:
    node = (await client.post("/api/v1/nodes", json={"label": "alice"})).json()
    stub_manager.mark_online(node["id"])
    stub_manager.set_exception(TimeoutError("agent silent"))
    resp = await client.post("/api/v1/tasks", json={
        "node_id": "alice", "site": "zhihu", "command": "hot", "timeout_sec": 5,
    })
    body = resp.json()
    assert body["status"] == "timeout"
    assert body["error_code"] == "TIMEOUT"


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

async def test_list_tasks_filters(client: AsyncClient, stub_manager: _StubManager) -> None:
    node = (await client.post("/api/v1/nodes", json={"label": "alice"})).json()
    stub_manager.mark_online(node["id"])
    stub_manager.set_result({"type": "result", "success": True, "items": []})

    for site in ("zhihu", "weibo", "zhihu"):
        await client.post("/api/v1/tasks", json={
            "node_id": "alice", "site": site, "command": "hot",
        })

    all_tasks = (await client.get("/api/v1/tasks")).json()
    assert len(all_tasks) == 3

    zhihu_only = (await client.get("/api/v1/tasks", params={"site": "zhihu"})).json()
    assert len(zhihu_only) == 2
    assert all(t["site"] == "zhihu" for t in zhihu_only)


async def test_get_task_records_unknown_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/tasks/does-not-exist/records")
    assert resp.status_code == 404
