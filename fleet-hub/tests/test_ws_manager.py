"""Unit tests for the WSManager — dispatch future plumbing, without a real socket."""

from __future__ import annotations

import asyncio

import pytest

from fleet_hub.ws.manager import NodeOffline, WSManager


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def close(self, code: int, reason: str = "") -> None:
        self.closed = (code, reason)


async def test_attach_and_detach():
    mgr = WSManager()
    ws = _FakeWebSocket()
    conn = await mgr.attach("node-1", "alice", ws)
    assert conn.node_id == "node-1"
    assert mgr.is_online("node-1")

    await mgr.detach("node-1")
    assert not mgr.is_online("node-1")


async def test_dispatch_and_resolve():
    mgr = WSManager()
    ws = _FakeWebSocket()
    await mgr.attach("node-1", "alice", ws)

    async def resolver():
        # Wait until the dispatch has registered its future, then resolve.
        await asyncio.sleep(0.05)
        ok = mgr.resolve("node-1", "task-x", {"type": "result", "success": True, "items": []})
        assert ok is True

    resolver_task = asyncio.create_task(resolver())

    result = await mgr.dispatch(
        "node-1",
        {"type": "collect", "task_id": "task-x", "site": "z", "command": "h"},
        timeout=1.0,
    )
    await resolver_task
    assert result["success"] is True
    assert ws.sent[0]["task_id"] == "task-x"


async def test_dispatch_timeout_cancels_future():
    mgr = WSManager()
    ws = _FakeWebSocket()
    await mgr.attach("node-1", "alice", ws)

    with pytest.raises(TimeoutError):
        await mgr.dispatch(
            "node-1",
            {"type": "collect", "task_id": "task-y", "site": "z", "command": "h"},
            timeout=0.1,
        )

    # Late resolve is a no-op, not a crash
    assert mgr.resolve("node-1", "task-y", {"type": "result", "success": True}) is False


async def test_dispatch_offline_raises():
    mgr = WSManager()
    with pytest.raises(NodeOffline):
        await mgr.dispatch("nope", {"task_id": "x"}, timeout=1.0)


async def test_detach_cancels_pending():
    mgr = WSManager()
    ws = _FakeWebSocket()
    await mgr.attach("node-1", "alice", ws)

    async def pending():
        with pytest.raises(NodeOffline):
            await mgr.dispatch(
                "node-1",
                {"type": "collect", "task_id": "task-z", "site": "z", "command": "h"},
                timeout=5.0,
            )

    pending_task = asyncio.create_task(pending())
    await asyncio.sleep(0.05)  # let dispatch register
    await mgr.detach("node-1")
    await pending_task


async def test_attach_replaces_prior():
    mgr = WSManager()
    ws1 = _FakeWebSocket()
    ws2 = _FakeWebSocket()
    await mgr.attach("node-1", "alice", ws1)
    await mgr.attach("node-1", "alice", ws2)
    # Old ws was closed
    assert ws1.closed is not None
    assert ws1.closed[0] == 4000
    assert mgr.is_online("node-1")
