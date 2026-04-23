"""WebSocket connection manager.

Responsibilities:
- Track active agent connections keyed by node_id.
- Dispatch collect frames and await their results via asyncio.Future.
- Clean up pending tasks when an agent disconnects.
- Timeouts are enforced by the caller (REST handler) using asyncio.wait_for.

This module is deliberately thin — it does not persist; the REST layer owns
Task rows and calls back into the manager when it needs to dispatch.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    """Raised when a collect frame can't be delivered (node offline, etc.)."""


class NodeOffline(DispatchError):
    pass


@dataclass
class _Connection:
    node_id: str
    label: str
    ws: WebSocket
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class WSManager:
    """Singleton-ish manager for all live agent WS connections."""

    def __init__(self) -> None:
        self._conns: dict[str, _Connection] = {}
        self._lock = asyncio.Lock()

    # -- connection lifecycle ------------------------------------------------

    async def attach(self, node_id: str, label: str, ws: WebSocket) -> _Connection:
        """Register an agent connection. Replaces any prior connection for the node."""
        async with self._lock:
            prev = self._conns.get(node_id)
            if prev is not None:
                logger.info("replacing prior connection for node %s", label)
                self._cancel_pending(prev, reason="replaced_by_new_connection")
                try:
                    await prev.ws.close(code=4000, reason="replaced")
                except Exception:
                    pass
            conn = _Connection(node_id=node_id, label=label, ws=ws)
            self._conns[node_id] = conn
            return conn

    async def detach(self, node_id: str) -> None:
        """Remove an agent connection and fail any pending dispatches for it."""
        async with self._lock:
            conn = self._conns.pop(node_id, None)
        if conn is not None:
            self._cancel_pending(conn, reason="disconnected")

    def _cancel_pending(self, conn: _Connection, *, reason: str) -> None:
        for task_id, fut in list(conn.pending.items()):
            if not fut.done():
                fut.set_exception(NodeOffline(reason))
        conn.pending.clear()

    # -- status queries ------------------------------------------------------

    def is_online(self, node_id: str) -> bool:
        return node_id in self._conns

    def online_node_ids(self) -> set[str]:
        return set(self._conns)

    # -- dispatch ------------------------------------------------------------

    async def dispatch(
        self,
        node_id: str,
        frame: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """Send a collect frame to a node and await its result.

        Raises NodeOffline if the node is not connected, TimeoutError on timeout.
        Returns the agent's result frame as a dict.
        """
        conn = self._conns.get(node_id)
        if conn is None:
            raise NodeOffline(f"node {node_id} is not connected")

        task_id = frame["task_id"]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        conn.pending[task_id] = fut

        try:
            await conn.ws.send_json(frame)
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"agent did not answer within {timeout}s") from None
        finally:
            conn.pending.pop(task_id, None)

    def resolve(self, node_id: str, task_id: str, payload: dict[str, Any]) -> bool:
        """Resolve a pending dispatch when a result frame arrives.

        Returns True if a waiting future was resolved, False if no one was waiting
        (late arrival after timeout/disconnect — caller should discard).
        """
        conn = self._conns.get(node_id)
        if conn is None:
            return False
        fut = conn.pending.pop(task_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result(payload)
        return True


manager = WSManager()
