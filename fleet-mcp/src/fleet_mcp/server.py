"""FastMCP server — all 6 MCP tools for the fleet."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastmcp import Context, FastMCP
from pydantic import Field

from fleet_mcp import admin_client
from fleet_mcp.config import settings
from fleet_mcp.schemas import (
    BroadcastNodeResult,
    BroadcastResult,
    DispatchResult,
    NodeInfo,
    SiteInfo,
    TaskStatusResult,
)
from fleet_mcp.security import (
    SITE_DESCRIPTIONS,
    SUPPORTED_SITES,
    audit_log,
    check_whitelist,
    rate_limiter,
    sanitize,
)

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "fleet-mcp",
    instructions=(
        "Fleet MCP server for dispatching opencli commands to a fleet of "
        "home laptops via opencli-admin. Use list_nodes to see which nodes "
        "are online, list_supported_sites for available commands, and "
        "dispatch/dispatch_best/broadcast to run them."
    ),
)


# ---------------------------------------------------------------------------
# Node-site mapping (Phase 1: loaded from YAML config)
# ---------------------------------------------------------------------------

def _load_node_sites() -> dict[str, list[str]]:
    path = Path(settings.node_sites_path)
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("Failed to load node_sites from %s", path, exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_items(records: list) -> list[dict]:
    """Extract item dicts from admin records."""
    items: list[dict] = []
    for rec in records:
        raw = rec.normalized_data or rec.raw_data or {}
        if isinstance(raw, dict) and "items" in raw:
            items.extend(raw["items"])
        elif isinstance(raw, list):
            items.extend(raw)
        else:
            items.append(raw)
    return items


def _node_id_from_admin(node) -> str:
    """Derive a short node_id from an AdminNode."""
    return node.label or node.id


async def _dispatch_to_node(
    node_id: str,
    site: str,
    command: str,
    args: dict[str, Any],
    timeout: float | None = None,
) -> DispatchResult:
    """Core dispatch logic shared by dispatch() and dispatch_best()."""
    t0 = time.monotonic()
    try:
        task, records = await admin_client.dispatch_and_wait(
            site=site,
            command=command,
            args=args,
            node_id=node_id,
            timeout=timeout,
        )
    except TimeoutError:
        duration_ms = int((time.monotonic() - t0) * 1000)
        audit_log(
            "dispatch", node_id=node_id, site=site, command=command,
            args=args, result="timeout", duration_ms=duration_ms,
        )
        return DispatchResult(
            success=False, node_id=node_id,
            error=f"Task timed out after {settings.task_timeout_sec}s",
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        audit_log(
            "dispatch", node_id=node_id, site=site, command=command,
            args=args, result="error", duration_ms=duration_ms,
        )
        return DispatchResult(
            success=False, node_id=node_id, error=str(exc),
        )

    duration_ms = int((time.monotonic() - t0) * 1000)

    if task.status != "completed":
        audit_log(
            "dispatch", node_id=node_id, site=site, command=command,
            args=args, result=task.status, duration_ms=duration_ms,
        )
        return DispatchResult(
            success=False, node_id=node_id, task_id=task.id,
            error=task.error_message or f"Task ended with status '{task.status}'",
            duration_ms=duration_ms,
        )

    items = sanitize(_extract_items(records))
    total = len(items)
    truncated = total > settings.max_items_inline
    if truncated:
        items = items[: settings.max_items_inline]

    audit_log(
        "dispatch", node_id=node_id, site=site, command=command,
        args=args, result="ok", duration_ms=duration_ms, items_count=total,
    )

    return DispatchResult(
        success=True,
        node_id=node_id,
        task_id=task.id,
        items=items,
        truncated=truncated,
        total_items=total,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool
async def list_nodes() -> dict[str, Any]:
    """List all registered fleet nodes and their status.

    Shows which laptops are online, what sites they're logged into,
    and their Chrome connection mode.
    """
    audit_log("list_nodes")
    admin_nodes = await admin_client.list_nodes()
    node_sites = _load_node_sites()

    nodes = []
    for n in admin_nodes:
        nid = _node_id_from_admin(n)
        nodes.append(
            NodeInfo(
                node_id=nid,
                label=n.label,
                online=n.status == "online",
                last_seen=n.last_seen_at,
                logged_in_sites=node_sites.get(nid, []),
                chrome_mode=n.mode,
            ).model_dump(mode="json")
        )

    return {"nodes": nodes}


@mcp.tool
async def list_supported_sites() -> dict[str, Any]:
    """List all whitelisted (site, command) pairs that can be dispatched.

    Use this to know what sites and commands are available before dispatching.
    """
    audit_log("list_supported_sites")
    sites = []
    for site, commands in sorted(SUPPORTED_SITES.items()):
        sites.append(
            SiteInfo(
                site=site,
                commands=sorted(commands),
                description=SITE_DESCRIPTIONS.get(site, site),
            ).model_dump()
        )
    return {"sites": sites}


@mcp.tool
async def dispatch(
    node_id: Annotated[str, Field(description="Target node ID (from list_nodes)")],
    site: Annotated[str, Field(description="Site name, e.g. 'xiaohongshu'")],
    command: Annotated[str, Field(description="Command to run, e.g. 'search'")],
    args: Annotated[
        dict[str, Any],
        Field(description="Command arguments, e.g. {\"q\": \"AI agents\", \"limit\": 20}"),
    ] = {},
) -> dict[str, Any]:
    """Run an opencli command on a specific node.

    The node must be online. Use list_nodes to find available nodes,
    and list_supported_sites to check which (site, command) pairs are allowed.
    Items are truncated to 50 by default; use get_task_status for full results.
    """
    # Whitelist check
    err = check_whitelist(site, command)
    if err:
        audit_log("dispatch", node_id=node_id, site=site, command=command, result="blocked")
        return DispatchResult(success=False, node_id=node_id, error=err).model_dump(mode="json")

    # Rate limit check
    err = rate_limiter.check(node_id)
    if err:
        audit_log("dispatch", node_id=node_id, site=site, command=command, result="rate_limited")
        return DispatchResult(success=False, node_id=node_id, error=err).model_dump(mode="json")

    result = await _dispatch_to_node(node_id, site, command, args)
    return result.model_dump(mode="json")


@mcp.tool
async def dispatch_best(
    site: Annotated[str, Field(description="Site name, e.g. 'zhihu'")],
    command: Annotated[str, Field(description="Command to run, e.g. 'hot'")],
    args: Annotated[
        dict[str, Any],
        Field(description="Command arguments"),
    ] = {},
) -> dict[str, Any]:
    """Auto-select the best node for a site and run a command.

    Picks an online node that is logged into the requested site.
    Prefers the least recently used node if multiple are available.
    """
    err = check_whitelist(site, command)
    if err:
        audit_log("dispatch_best", site=site, command=command, result="blocked")
        return DispatchResult(success=False, error=err).model_dump(mode="json")

    # Find eligible nodes
    admin_nodes = await admin_client.list_nodes()
    node_sites = _load_node_sites()

    candidates = []
    for n in admin_nodes:
        nid = _node_id_from_admin(n)
        if n.status != "online":
            continue
        sites_for_node = node_sites.get(nid, [])
        if site in sites_for_node:
            candidates.append((n, nid))

    if not candidates:
        # Build a helpful error
        online = [_node_id_from_admin(n) for n in admin_nodes if n.status == "online"]
        all_with_site = [
            nid for nid, sites_list in node_sites.items() if site in sites_list
        ]
        if not online:
            msg = "No nodes are online"
        elif not all_with_site:
            msg = f"No nodes are configured for site '{site}'. Update node_sites.yaml."
        else:
            offline_with_site = set(all_with_site) - set(online)
            msg = (
                f"Nodes configured for '{site}' ({', '.join(all_with_site)}) "
                f"are all offline. Online nodes: {', '.join(online)}"
            )
        audit_log("dispatch_best", site=site, command=command, result="no_node")
        return DispatchResult(success=False, error=msg).model_dump(mode="json")

    # Pick LRU — use last_seen as proxy (least recently seen = least recently used)
    candidates.sort(key=lambda x: x[0].last_seen_at or 0)
    node, node_id = candidates[0]

    # Rate limit
    err = rate_limiter.check(node_id)
    if err:
        audit_log("dispatch_best", node_id=node_id, site=site, command=command, result="rate_limited")
        return DispatchResult(success=False, node_id=node_id, error=err).model_dump(mode="json")

    result = await _dispatch_to_node(node_id, site, command, args)
    return result.model_dump(mode="json")


@mcp.tool
async def broadcast(
    site: Annotated[str, Field(description="Site name")],
    command: Annotated[str, Field(description="Command to run")],
    args: Annotated[
        dict[str, Any],
        Field(description="Command arguments"),
    ] = {},
) -> dict[str, Any]:
    """Run a command on ALL online nodes logged into the given site.

    Useful for multi-account data collection. Does not fail the whole call
    if one node errors — each node's result is reported independently.
    """
    err = check_whitelist(site, command)
    if err:
        audit_log("broadcast", site=site, command=command, result="blocked")
        return BroadcastResult(
            total_nodes=0,
            results=[BroadcastNodeResult(node_id="*", success=False, error=err)],
        ).model_dump(mode="json")

    admin_nodes = await admin_client.list_nodes()
    node_sites = _load_node_sites()

    targets: list[tuple[str, str]] = []  # (node_id, node_admin_id)
    for n in admin_nodes:
        nid = _node_id_from_admin(n)
        if n.status == "online" and site in node_sites.get(nid, []):
            targets.append((nid, n.id))

    if not targets:
        audit_log("broadcast", site=site, command=command, result="no_nodes")
        return BroadcastResult(total_nodes=0).model_dump(mode="json")

    # Dispatch to all in parallel with per-node timeout
    timeout = settings.broadcast_timeout_sec

    async def _run_one(node_id: str) -> BroadcastNodeResult:
        rl_err = rate_limiter.check(node_id)
        if rl_err:
            return BroadcastNodeResult(node_id=node_id, success=False, error=rl_err)
        result = await _dispatch_to_node(node_id, site, command, args, timeout=timeout)
        return BroadcastNodeResult(
            node_id=node_id,
            success=result.success,
            items=result.items,
            error=result.error,
        )

    results = await asyncio.gather(
        *[_run_one(nid) for nid, _ in targets],
        return_exceptions=True,
    )

    node_results: list[BroadcastNodeResult] = []
    for i, r in enumerate(results):
        nid = targets[i][0]
        if isinstance(r, Exception):
            node_results.append(
                BroadcastNodeResult(node_id=nid, success=False, error=str(r))
            )
        else:
            node_results.append(r)

    audit_log(
        "broadcast", site=site, command=command,
        result="ok", items_count=sum(len(r.items) for r in node_results),
    )

    return BroadcastResult(
        total_nodes=len(targets), results=node_results,
    ).model_dump(mode="json")


@mcp.tool
async def get_task_status(
    task_id: Annotated[str, Field(description="Task ID from a previous dispatch call")],
) -> dict[str, Any]:
    """Get the full, untruncated result of a previous dispatch.

    Use this when a dispatch result was truncated and you need all items,
    or to check the status of a long-running task.
    """
    try:
        task = await admin_client.get_task(task_id)
    except Exception as exc:
        audit_log("get_task_status", result="error")
        return TaskStatusResult(
            task_id=task_id, status="error", error=str(exc),
        ).model_dump(mode="json")

    items: list[dict] = []
    if task.status == "completed":
        records = await admin_client.get_records(task_id)
        items = sanitize(_extract_items(records))

    audit_log("get_task_status", result="ok", items_count=len(items))

    return TaskStatusResult(
        task_id=task_id,
        status=task.status,
        items=items,
        total_items=len(items),
        error=task.error_message,
    ).model_dump(mode="json")
