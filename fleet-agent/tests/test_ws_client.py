"""Integration test: fleet-agent AgentClient against an in-process fake hub."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import websockets

from fleet_agent import login_detect, ws_client
from fleet_agent.runner import RunResult


class FakeHub:
    """A minimal WS server that mimics fleet-hub's /api/v1/nodes/ws.

    Holds the last received register frame and exposes a `dispatch_collect`
    method to send a collect and await the result frame.
    """

    def __init__(self) -> None:
        self.register_frame: dict[str, Any] | None = None
        self.result_frames: list[dict[str, Any]] = []
        self._server = None
        self._host = "127.0.0.1"
        self._port = 0
        self._client_ws = None
        self._connected = asyncio.Event()
        self._result_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    async def start(self) -> None:
        async def handler(ws):
            # Expect register frame first.
            try:
                first = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                await ws.close(code=4002)
                return
            self.register_frame = json.loads(first)
            await ws.send(json.dumps({
                "type": "registered",
                "node_id": "fake-id",
                "label": "fake-label",
            }))
            self._client_ws = ws
            self._connected.set()
            try:
                async for msg in ws:
                    frame = json.loads(msg)
                    if frame.get("type") == "result":
                        self.result_frames.append(frame)
                        self._result_queue.put_nowait(frame)
                    elif frame.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
            except websockets.ConnectionClosed:
                pass

        # Let websockets pick a port, then read it back.
        server = await websockets.serve(
            handler, self._host, 0,
            process_request=None,
        )
        self._server = server
        self._port = server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def wait_registered(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    async def send_collect(self, task_id: str, site: str, command: str) -> None:
        assert self._client_ws is not None
        await self._client_ws.send(json.dumps({
            "type": "collect",
            "task_id": task_id,
            "site": site,
            "command": command,
            "args": {},
            "positional_args": [],
            "format": "json",
            "timeout": 30,
        }))

    async def next_result(self, timeout: float = 5.0) -> dict[str, Any]:
        return await asyncio.wait_for(self._result_queue.get(), timeout=timeout)


@pytest.fixture
async def hub():
    h = FakeHub()
    await h.start()
    try:
        yield h
    finally:
        await h.stop()


@pytest.fixture
def patched_agent(monkeypatch, hub: FakeHub):
    """Patch agent internals: probe is instant, opencli runs are mocked."""
    async def _fake_probe(*_a, **_kw):
        return ["zhihu", "xiaohongshu"]

    async def _fake_version():
        return "1.7.7"

    monkeypatch.setattr(login_detect, "detect_logged_in_sites", _fake_probe)
    monkeypatch.setattr(ws_client, "detect_logged_in_sites", _fake_probe)

    # settings is a module-level singleton; patch fields for this test.
    from fleet_agent import config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "central_url", hub.url)
    monkeypatch.setattr(cfg_mod.settings, "node_token", "test-token")
    monkeypatch.setattr(cfg_mod.settings, "node_label", "test-node")
    monkeypatch.setattr(cfg_mod.settings, "ws_reconnect_min_sec", 0.05)
    monkeypatch.setattr(cfg_mod.settings, "ws_reconnect_max_sec", 0.1)
    monkeypatch.setattr(cfg_mod.settings, "ws_ping_interval_sec", 60)
    monkeypatch.setattr(cfg_mod.settings, "login_probe_timeout_sec", 1)


async def test_register_and_dispatch_roundtrip(hub: FakeHub, patched_agent, monkeypatch):
    """End-to-end: register, receive collect, run opencli (mocked), send result."""
    async def _fake_run(_bin: str, **_kw):
        return RunResult(
            success=True, items=[{"title": "T1"}, {"title": "T2"}],
            exit_code=0, duration_ms=123,
        )

    monkeypatch.setattr(ws_client, "run_opencli", _fake_run)

    # Short-circuit the agent's own version detection.
    async def _fake_version(self):
        return "1.7.7"
    monkeypatch.setattr(ws_client.AgentClient, "_detect_opencli_version", _fake_version)

    client = ws_client.AgentClient()
    run_task = asyncio.create_task(client.run())

    try:
        await hub.wait_registered(timeout=3.0)
        assert hub.register_frame is not None
        assert hub.register_frame["token"] == "test-token"
        assert hub.register_frame["mode"] == "bridge"
        assert set(hub.register_frame["logged_in_sites"]) == {"zhihu", "xiaohongshu"}

        await hub.send_collect("task-abc", "zhihu", "hot")
        result = await hub.next_result(timeout=3.0)
        assert result["task_id"] == "task-abc"
        assert result["success"] is True
        assert len(result["items"]) == 2
    finally:
        await client.stop()
        # Break out of the reconnect loop.
        run_task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await run_task


async def test_runner_exception_becomes_generic_frame(
    hub: FakeHub, patched_agent, monkeypatch
):
    """Regression: an exception inside `run_opencli` must still produce a
    result frame back to the hub, not disappear into an asyncio Task that
    prints "Task exception was never retrieved" at GC while the hub hangs
    waiting for the reply.
    """
    async def _fake_run(_bin: str, **_kw):
        raise RuntimeError("unexpected runner bug")

    monkeypatch.setattr(ws_client, "run_opencli", _fake_run)

    async def _fake_version(self):
        return "1.7.7"
    monkeypatch.setattr(ws_client.AgentClient, "_detect_opencli_version", _fake_version)

    client = ws_client.AgentClient()
    run_task = asyncio.create_task(client.run())
    try:
        await hub.wait_registered(timeout=3.0)
        await hub.send_collect("task-boom", "zhihu", "hot")
        result = await hub.next_result(timeout=3.0)
        assert result["task_id"] == "task-boom"
        assert result["success"] is False
        assert result["error"]["code"] == "GENERIC"
        assert "RuntimeError" in result["error"]["message"]
        assert "unexpected runner bug" in result["error"]["message"]
    finally:
        await client.stop()
        run_task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await run_task


async def test_auth_required_result_propagates(hub: FakeHub, patched_agent, monkeypatch):
    async def _fake_run(_bin: str, **_kw):
        return RunResult(
            success=False, items=[], exit_code=77, duration_ms=50,
            error_code="AUTH_REQUIRED", error_message="logged out",
        )

    monkeypatch.setattr(ws_client, "run_opencli", _fake_run)

    async def _fake_version(self):
        return "1.7.7"
    monkeypatch.setattr(ws_client.AgentClient, "_detect_opencli_version", _fake_version)

    client = ws_client.AgentClient()
    run_task = asyncio.create_task(client.run())
    try:
        await hub.wait_registered(timeout=3.0)
        await hub.send_collect("task-x", "zhihu", "hot")
        result = await hub.next_result(timeout=3.0)
        assert result["success"] is False
        assert result["error"]["code"] == "AUTH_REQUIRED"
        assert result["error"]["exit_code"] == 77
    finally:
        await client.stop()
        run_task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await run_task
