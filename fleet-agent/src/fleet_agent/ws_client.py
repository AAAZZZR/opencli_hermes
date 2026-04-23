"""WebSocket client that connects to fleet-hub and dispatches opencli runs.

Lifecycle:
  1. Connect to central WS URL.
  2. Send register frame with token + detected logged-in sites.
  3. Wait for 'registered' ack, else bail.
  4. Loop: handle collect / ping frames. For collect, run opencli in a
     background task and send back a result frame when done.
  5. On disconnect or error, reconnect with exponential backoff.

Multiple tasks can run concurrently on the same agent — each dispatch is
fired-and-forgotten; the result is sent back over the same WS once ready.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from fleet_agent import __version__
from fleet_agent.config import HOST_OS, settings
from fleet_agent.login_detect import detect_logged_in_sites
from fleet_agent.runner import run_opencli

logger = logging.getLogger(__name__)


DEFAULT_CANDIDATE_SITES = [
    "xiaohongshu", "zhihu", "bilibili", "weibo", "twitter", "reddit",
]


class AgentClient:
    def __init__(self) -> None:
        self._backoff = settings.ws_reconnect_min_sec
        self._stop = asyncio.Event()
        self._opencli_version = "unknown"
        self._logged_in_sites: list[str] = []
        self._in_flight: set[asyncio.Task] = set()

    async def stop(self) -> None:
        self._stop.set()

    # -----------------------------------------------------------------------
    # Startup probes
    # -----------------------------------------------------------------------

    async def _detect_opencli_version(self) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                settings.opencli_bin, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            return out.decode().strip() or "unknown"
        except Exception:
            return "unknown"

    async def _probe_login(self) -> list[str]:
        return await detect_logged_in_sites(
            settings.opencli_bin,
            candidate_sites=DEFAULT_CANDIDATE_SITES,
            timeout_sec=settings.login_probe_timeout_sec,
        )

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    async def run(self) -> None:
        if not settings.node_token or not settings.central_url:
            raise RuntimeError("NODE_TOKEN and CENTRAL_URL must be set")

        # Probe once at startup; re-probe at each reconnect would be too noisy.
        self._opencli_version = await self._detect_opencli_version()
        logger.info("opencli version: %s", self._opencli_version)
        self._logged_in_sites = await self._probe_login()
        logger.info("logged in sites: %s", self._logged_in_sites)

        while not self._stop.is_set():
            try:
                await self._connect_once()
            except Exception as exc:
                logger.warning("WS loop error: %s", exc)
            if self._stop.is_set():
                break
            await self._sleep_backoff()

    async def _sleep_backoff(self) -> None:
        jitter = random.uniform(0.8, 1.2)
        sleep = min(self._backoff * jitter, settings.ws_reconnect_max_sec)
        logger.info("reconnecting in %.1fs", sleep)
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=sleep)
        except asyncio.TimeoutError:
            pass
        self._backoff = min(self._backoff * 2, settings.ws_reconnect_max_sec)

    async def _connect_once(self) -> None:
        url = settings.ws_url
        logger.info("connecting to %s", url)
        async with websockets.connect(
            url,
            ping_interval=settings.ws_ping_interval_sec,
            ping_timeout=settings.ws_ping_timeout_sec,
            max_size=None,
            user_agent_header=f"fleet-agent/{__version__}",
        ) as ws:
            await self._register(ws)
            # Reset backoff on successful handshake.
            self._backoff = settings.ws_reconnect_min_sec
            await self._handle_frames(ws)

    async def _register(self, ws) -> None:
        frame = {
            "type": "register",
            "token": settings.node_token,
            "mode": settings.agent_mode,
            "os": HOST_OS,
            "logged_in_sites": self._logged_in_sites,
            "opencli_version": self._opencli_version,
        }
        await ws.send(json.dumps(frame))
        first = await asyncio.wait_for(ws.recv(), timeout=30.0)
        resp = json.loads(first)
        if resp.get("type") != "registered":
            raise RuntimeError(f"unexpected register response: {resp}")
        logger.info("registered as node %s (%s)", resp.get("label"), resp.get("node_id"))

    async def _handle_frames(self, ws) -> None:
        try:
            async for msg in ws:
                try:
                    frame = json.loads(msg)
                except json.JSONDecodeError:
                    logger.warning("non-json frame from hub")
                    continue
                ftype = frame.get("type")
                if ftype == "collect":
                    task = asyncio.create_task(self._run_collect(ws, frame))
                    self._in_flight.add(task)
                    task.add_done_callback(self._in_flight.discard)
                elif ftype == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                elif ftype == "pong":
                    pass
                else:
                    logger.debug("ignoring frame type %r", ftype)
        except ConnectionClosed:
            logger.info("connection closed")
        finally:
            # Let any in-flight tasks finish — they'll try to send results;
            # if the socket is dead the send will raise and they'll log.
            for t in list(self._in_flight):
                if not t.done():
                    t.cancel()
            self._in_flight.clear()

    async def _run_collect(self, ws, frame: dict[str, Any]) -> None:
        task_id = frame.get("task_id") or ""
        site = frame.get("site") or ""
        command = frame.get("command") or ""
        args = frame.get("args") or {}
        positional = frame.get("positional_args") or []
        fmt = frame.get("format") or "json"
        timeout = float(frame.get("timeout") or 120)

        logger.info("dispatch task=%s %s/%s", task_id, site, command)
        result = await run_opencli(
            settings.opencli_bin,
            site=site, command=command,
            args=args, positional_args=list(positional),
            format=fmt, timeout_sec=timeout,
        )
        out_frame = result.to_frame(task_id)
        try:
            await ws.send(json.dumps(out_frame))
        except ConnectionClosed:
            logger.warning("ws closed before result for task %s could be sent", task_id)
