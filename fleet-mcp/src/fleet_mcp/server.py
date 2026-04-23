"""FastMCP server — all 6 MCP tools for the fleet.

All tools eventually call into `fleet_mcp.hub_client`, which wraps the
fleet-hub REST API. Hub returns node listings with `logged_in_sites` baked
in, so fleet-mcp no longer keeps a separate mapping file.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from fleet_mcp import hub_client
from fleet_mcp.config import settings
from fleet_mcp.schemas import (
    BroadcastNodeResult,
    BroadcastResult,
    DispatchResult,
    HubNode,
    HubTaskResult,
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
        "home laptops via fleet-hub. Use list_nodes to see which nodes are "
        "online, list_supported_sites for available commands, and "
        "dispatch/dispatch_best/broadcast to run them."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate_items(items: list[dict]) -> tuple[list[dict], bool]:
    """Return (possibly truncated items, truncated_flag)."""
    total = len(items)
    if total <= settings.max_items_inline:
        return items, False
    return items[: settings.max_items_inline], True


def _result_from_hub(task: HubTaskResult) -> DispatchResult:
    """Map a HubTaskResult to the DispatchResult returned to Hermes."""
    success = task.status == "completed"
    items = sanitize(task.items) if success else []
    items, truncated = _truncate_items(items) if success else (items, False)
    return DispatchResult(
        success=success,
        node_id=task.node_id,
        task_id=task.id,
        items=items,
        truncated=truncated,
        total_items=task.items_stored or len(items),
        duration_ms=task.duration_ms,
        error=task.error_message if not success else None,
        error_code=task.error_code if not success else None,
        exit_code=task.exit_code,
    )


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool
async def list_nodes() -> dict[str, Any]:
    """List all registered fleet nodes and their status.

    Shows which laptops are online, which sites they're logged into,
    and their Chrome mode / OS / opencli version.
    """
    audit_log("list_nodes")
    nodes = await hub_client.list_nodes()
    return {
        "nodes": [
            NodeInfo(
                node_id=n.label,   # label is the human-friendly identifier
                label=n.label,
                online=n.status == "online",
                last_seen=n.last_seen_at,
                logged_in_sites=n.logged_in_sites,
                chrome_mode=n.mode,
                os=n.os,
                opencli_version=n.opencli_version,
            ).model_dump(mode="json")
            for n in nodes
        ],
    }


@mcp.tool
async def list_supported_sites() -> dict[str, Any]:
    """List all whitelisted (site, command) pairs that can be dispatched."""
    audit_log("list_supported_sites")
    return {
        "sites": [
            SiteInfo(
                site=site,
                commands=sorted(commands),
                description=SITE_DESCRIPTIONS.get(site, site),
            ).model_dump()
            for site, commands in sorted(SUPPORTED_SITES.items())
        ],
    }


@mcp.tool
async def dispatch(
    node_id: Annotated[
        str,
        Field(description="Target node — use label (from list_nodes) or UUID"),
    ],
    site: Annotated[str, Field(description="Site name, e.g. 'xiaohongshu'")],
    command: Annotated[str, Field(description="Command to run, e.g. 'search'")],
    args: Annotated[
        dict[str, Any],
        Field(description='Command flags, e.g. {"limit": 10}'),
    ] = {},
    positional_args: Annotated[
        list[Any],
        Field(description='Positional args (before flags), e.g. ["AI agents"]'),
    ] = [],
) -> dict[str, Any]:
    """Run an opencli command on a specific node.

    Items are truncated to MAX_ITEMS_INLINE (default 50) in the response; use
    get_task_status(task_id) to retrieve the full list.
    """
    err = check_whitelist(site, command)
    if err:
        audit_log("dispatch", node_id=node_id, site=site, command=command, result="blocked")
        return DispatchResult(success=False, node_id=node_id, error=err).model_dump(mode="json")

    err = rate_limiter.check(node_id)
    if err:
        audit_log("dispatch", node_id=node_id, site=site, command=command, result="rate_limited")
        return DispatchResult(success=False, node_id=node_id, error=err).model_dump(mode="json")

    try:
        task = await hub_client.dispatch(
            node_id=node_id, site=site, command=command,
            args=args, positional_args=positional_args,
            timeout=settings.task_timeout_sec,
        )
    except Exception as exc:
        audit_log("dispatch", node_id=node_id, site=site, command=command, result="error")
        return DispatchResult(
            success=False, node_id=node_id, error=f"hub error: {exc}",
        ).model_dump(mode="json")

    result = _result_from_hub(task)
    audit_log(
        "dispatch", node_id=node_id, site=site, command=command, args=args,
        result="ok" if result.success else (result.error_code or "failed"),
        duration_ms=result.duration_ms, items_count=result.total_items,
    )
    return result.model_dump(mode="json")


@mcp.tool
async def dispatch_best(
    site: Annotated[str, Field(description="Site name, e.g. 'zhihu'")],
    command: Annotated[str, Field(description="Command to run, e.g. 'hot'")],
    args: Annotated[dict[str, Any], Field(description="Command flags")] = {},
    positional_args: Annotated[list[Any], Field(description="Positional args")] = [],
) -> dict[str, Any]:
    """Auto-select the best node for a site and run a command.

    Picks an online node that is logged into the requested site. Prefers the
    least recently used node if multiple are available.
    """
    err = check_whitelist(site, command)
    if err:
        audit_log("dispatch_best", site=site, command=command, result="blocked")
        return DispatchResult(success=False, error=err).model_dump(mode="json")

    nodes = await hub_client.list_nodes()
    online_for_site = [n for n in nodes if n.status == "online" and site in n.logged_in_sites]

    if not online_for_site:
        online = [n.label for n in nodes if n.status == "online"]
        all_with_site = [n.label for n in nodes if site in n.logged_in_sites]
        if not online:
            msg = "No nodes are online"
        elif not all_with_site:
            msg = f"No nodes are logged in to '{site}'"
        else:
            msg = (
                f"Nodes logged in to '{site}' ({', '.join(all_with_site)}) are all offline. "
                f"Online nodes: {', '.join(online)}"
            )
        audit_log("dispatch_best", site=site, command=command, result="no_node")
        return DispatchResult(success=False, error=msg).model_dump(mode="json")

    # Least-recently-used — stable datetime comparison.
    _SENTINEL = datetime.min.replace(tzinfo=timezone.utc)
    online_for_site.sort(key=lambda n: n.last_seen_at or _SENTINEL)
    chosen = online_for_site[0]

    err = rate_limiter.check(chosen.label)
    if err:
        audit_log(
            "dispatch_best", node_id=chosen.label, site=site, command=command,
            result="rate_limited",
        )
        return DispatchResult(
            success=False, node_id=chosen.label, error=err,
        ).model_dump(mode="json")

    try:
        task = await hub_client.dispatch(
            node_id=chosen.label, site=site, command=command,
            args=args, positional_args=positional_args,
            timeout=settings.task_timeout_sec,
        )
    except Exception as exc:
        audit_log(
            "dispatch_best", node_id=chosen.label, site=site, command=command,
            result="error",
        )
        return DispatchResult(
            success=False, node_id=chosen.label, error=f"hub error: {exc}",
        ).model_dump(mode="json")

    result = _result_from_hub(task)
    audit_log(
        "dispatch_best", node_id=chosen.label, site=site, command=command, args=args,
        result="ok" if result.success else (result.error_code or "failed"),
        duration_ms=result.duration_ms, items_count=result.total_items,
    )
    return result.model_dump(mode="json")


@mcp.tool
async def broadcast(
    site: Annotated[str, Field(description="Site name")],
    command: Annotated[str, Field(description="Command to run")],
    args: Annotated[dict[str, Any], Field(description="Command flags")] = {},
    positional_args: Annotated[list[Any], Field(description="Positional args")] = [],
) -> dict[str, Any]:
    """Run a command on all online nodes logged into the given site.

    Useful for multi-account data collection. Does not fail the whole call if
    one node errors — each node's result is reported independently.
    """
    err = check_whitelist(site, command)
    if err:
        audit_log("broadcast", site=site, command=command, result="blocked")
        return BroadcastResult(
            total_nodes=0,
            results=[BroadcastNodeResult(node_id="*", success=False, error=err)],
        ).model_dump(mode="json")

    nodes = await hub_client.list_nodes()
    targets = [n for n in nodes if n.status == "online" and site in n.logged_in_sites]
    if not targets:
        audit_log("broadcast", site=site, command=command, result="no_nodes")
        return BroadcastResult(total_nodes=0).model_dump(mode="json")

    timeout = settings.broadcast_timeout_sec

    async def _run_one(node: HubNode) -> BroadcastNodeResult:
        rl_err = rate_limiter.check(node.label)
        if rl_err:
            return BroadcastNodeResult(node_id=node.label, success=False, error=rl_err)
        try:
            task = await hub_client.dispatch(
                node_id=node.label, site=site, command=command,
                args=args, positional_args=positional_args,
                timeout=timeout,
            )
        except Exception as exc:
            return BroadcastNodeResult(
                node_id=node.label, success=False, error=f"hub error: {exc}",
            )
        if task.status == "completed":
            items = sanitize(task.items)
            items, _ = _truncate_items(items)
            return BroadcastNodeResult(node_id=node.label, success=True, items=items)
        return BroadcastNodeResult(
            node_id=node.label, success=False,
            error=task.error_message or task.status,
            error_code=task.error_code,
        )

    results = await asyncio.gather(
        *[_run_one(n) for n in targets],
        return_exceptions=True,
    )
    node_results: list[BroadcastNodeResult] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            node_results.append(
                BroadcastNodeResult(node_id=targets[i].label, success=False, error=str(r))
            )
        else:
            node_results.append(r)

    ok_count = sum(1 for r in node_results if r.success)
    if ok_count == len(node_results):
        agg = "ok"
    elif ok_count == 0:
        agg = "all_failed"
    else:
        agg = "partial"
    audit_log(
        "broadcast", site=site, command=command, result=agg,
        items_count=sum(len(r.items) for r in node_results),
    )
    return BroadcastResult(total_nodes=len(targets), results=node_results).model_dump(mode="json")


@mcp.tool
async def get_task_status(
    task_id: Annotated[str, Field(description="Task ID from a previous dispatch call")],
) -> dict[str, Any]:
    """Get full (untruncated) records for a previous task.

    Useful when dispatch() returned `truncated=true`.
    """
    try:
        task = await hub_client.get_task(task_id)
    except Exception as exc:
        audit_log("get_task_status", result="error")
        return TaskStatusResult(
            task_id=task_id, status="error", error=str(exc),
        ).model_dump(mode="json")

    items: list[dict] = []
    if task.status == "completed":
        try:
            record_list = await hub_client.get_task_records(task_id, limit=5000)
            items = sanitize(record_list.items)
        except Exception as exc:
            logger.warning("failed to fetch records for %s: %s", task_id, exc)

    audit_log("get_task_status", result="ok", items_count=len(items))
    return TaskStatusResult(
        task_id=task_id,
        status=task.status,
        items=items,
        total_items=len(items),
        error=task.error_message,
        error_code=task.error_code,
    ).model_dump(mode="json")
