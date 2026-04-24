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
    allowed_commands_for,
    audit_log,
    blocked_commands_for,
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
    """List every site fleet-mcp will dispatch to, with the sub-commands you may call.

    Use `allowed_commands` to pick a sub-command for `dispatch` / `dispatch_best` /
    `broadcast`. `blocked_commands` are write / mutation operations (post, reply,
    comment, like, follow, subscribe, upvote, publish, delete, add-cart, AI-chat
    ask/send, etc.) that fleet-mcp refuses to run on your account. Unknown
    sub-commands are also rejected with a hint back to the allowed list.
    """
    audit_log("list_supported_sites")
    return {
        "sites": [
            SiteInfo(
                site=site,
                description=SITE_DESCRIPTIONS.get(site, site),
                allowed_commands=allowed_commands_for(site),
                blocked_commands=blocked_commands_for(site),
            ).model_dump()
            for site in sorted(SUPPORTED_SITES)
        ],
    }


@mcp.tool
async def dispatch(
    node_id: Annotated[
        str,
        Field(description="Target node — use label (from list_nodes) or UUID"),
    ],
    site: Annotated[str, Field(description="Site key from list_supported_sites (e.g. 'reddit', 'zhihu', 'arxiv'). Call list_supported_sites first if unsure.")],
    command: Annotated[str, Field(description="Sub-command for this site. MUST come from that site's `allowed_commands` in list_supported_sites — e.g. 'hot', 'search', 'read' (single post/article by id), 'user', 'question'. fleet-mcp rejects unknown commands with a hint.")],
    args: Annotated[
        dict[str, Any],
        Field(description='Command flags as a dict, e.g. {"limit": 10, "subreddit": "wallstreetbets"}'),
    ] = {},
    positional_args: Annotated[
        list[Any],
        Field(description='Positional args that come BEFORE flags. For single-item reads this is usually [item_id]: reddit read ["1k4j2m3"], zhihu question ["430300881"], bilibili video ["BV1xxx"].'),
    ] = [],
) -> dict[str, Any]:
    """Run a specific opencli sub-command on a specific node.

    Call `list_supported_sites` FIRST if you don't already know the exact
    command name for this site — every site has an `allowed_commands` list
    (reads) and `blocked_commands` list (writes fleet-mcp refuses).

    Typical command patterns across sites:
      - `hot` / `search` / `trending` → list of items
      - `read <id>` / `article <id>` / `question <id>` / `video <id>` →
         one specific item WITH its comments/replies
      - `user <handle>` / `profile <handle>` → profile info

    Items are truncated to MAX_ITEMS_INLINE (default 50); use
    `get_task_status(task_id)` for the full list from fleet-hub.
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
    site: Annotated[str, Field(description="Site key from list_supported_sites. Call list_supported_sites first if unsure.")],
    command: Annotated[str, Field(description="Sub-command from that site's `allowed_commands` in list_supported_sites. Common reads: hot, search, read (single item by id), user, article, question, video, profile. Unknown commands are rejected with a hint.")],
    args: Annotated[dict[str, Any], Field(description='Command flags as dict, e.g. {"limit": 10}')] = {},
    positional_args: Annotated[list[Any], Field(description="Positional args (usually [item_id] for single-item reads like reddit/read, bilibili/video, zhihu/question).")] = [],
) -> dict[str, Any]:
    """Auto-pick the best online node for a site and run a command.

    Call `list_supported_sites` FIRST for the exact command name — every site
    has `allowed_commands` (reads) and `blocked_commands` (writes blocked).
    fleet-mcp rejects unknown sub-commands before dispatch with a hint.

    Prefers LRU node that's logged into the site. Falls back to any online
    node if nobody reports the site as logged-in (fine for arxiv, wikipedia,
    bloomberg, hackernews, etc. that need no login — AUTH_REQUIRED propagates
    back from opencli if login actually is needed).
    """
    err = check_whitelist(site, command)
    if err:
        audit_log("dispatch_best", site=site, command=command, result="blocked")
        return DispatchResult(success=False, error=err).model_dump(mode="json")

    nodes = await hub_client.list_nodes()
    online_nodes = [n for n in nodes if n.status == "online"]
    online_for_site = [n for n in online_nodes if site in n.logged_in_sites]

    if not online_for_site:
        # No node is verified-logged-in for this site. Fall back to any online
        # node — many sites (arxiv, wikipedia, bloomberg, hackernews, ...) need
        # no login, and login_detect only probes a handful of sites at register
        # time. If the site actually needs login and the chosen node lacks it,
        # opencli will return AUTH_REQUIRED and the error propagates back
        # through WS → hub → here naturally.
        if not online_nodes:
            audit_log("dispatch_best", site=site, command=command, result="no_node")
            return DispatchResult(
                success=False, error="No nodes are online"
            ).model_dump(mode="json")
        online_for_site = online_nodes

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
    online_nodes = [n for n in nodes if n.status == "online"]
    targets = [n for n in online_nodes if site in n.logged_in_sites]
    if not targets:
        # Same reasoning as dispatch_best: many sites don't need login; fall
        # back to every online node. AUTH_REQUIRED propagates per-node.
        targets = online_nodes
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
