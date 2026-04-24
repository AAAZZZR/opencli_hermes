# fleet-mcp

MCP adapter that exposes 6 tools to Hermes (or any MCP client) for
dispatching `@jackwener/opencli` commands across a fleet of home laptops
via [`fleet-hub`](../fleet-hub).

## Install

```bash
uv venv .venv && uv pip install -e ".[dev]"
cp .env.example .env
# Edit .env if fleet-hub isn't on http://localhost:8031
```

## Run (as MCP stdio server)

```bash
.venv/bin/python -m fleet_mcp
```

Normally Hermes launches this process itself. See `docs/hermes-config.yaml`
in the monorepo root.

## Tools

| Tool | Input | Output |
|------|-------|--------|
| `list_nodes()` | — | `{nodes: [{node_id, online, logged_in_sites, ...}]}` |
| `list_supported_sites()` | — | `{sites: [{site, description, blocked_commands}]}` |
| `dispatch(node_id, site, command, args, positional_args)` | dict | `DispatchResult` |
| `dispatch_best(site, command, args, positional_args)` | dict | `DispatchResult` |
| `broadcast(site, command, args, positional_args)` | dict | `BroadcastResult` |
| `get_task_status(task_id)` | task id | `TaskStatusResult` (full, untruncated items) |

`DispatchResult`:

```json
{
  "success": true,
  "node_id": "alice-mbp",
  "task_id": "<uuid>",
  "items": [...],
  "truncated": false,
  "total_items": 12,
  "duration_ms": 4521,
  "error": null,
  "error_code": null,
  "exit_code": 0
}
```

On failure, `success=false`, `items=[]`, `error_code` follows OpenCLI's
taxonomy (`AUTH_REQUIRED`, `TIMEOUT`, `EMPTY`, `SERVICE_UNAVAILABLE`,
`CONFIG`, `GENERIC`).

Items are capped at `MAX_ITEMS_INLINE` (default 50) in tool responses.
Use `get_task_status(task_id)` to retrieve the full normalized record
list from the hub.

## Security (deny-list model)

- **Sites allow-list** (`SUPPORTED_SITES` in `src/fleet_mcp/security.py`) —
  flat frozenset of every opencli site (101 as of v1.7.7). Unknown site
  rejected.
- **Global forbidden verbs** (`FORBIDDEN_GLOBAL`) — framework commands
  that never run: `browser, eval, register, install, plugin, daemon,
  adapter, synthesize, record, exec, shell`.
- **Per-site write blocks** (`FORBIDDEN_PER_SITE`) — every write-type
  sub-command that would mutate a user account (`post`, `reply`,
  `comment`, `like`, `follow`, `subscribe`, `upvote`, `publish`,
  `delete`, `add-cart`, AI-chat `ask`/`send`/`new`, …). 37 sites have
  at least one blocked command. Reads are implicitly allowed — unknown
  sub-commands get rejected downstream by opencli itself.
- **Rate limit**: 10 req/min per node + 60 req/min globally (token bucket).
- **Audit log**: JSONL at `~/.fleet-mcp/audit.log`. Args are hashed, not
  raw — they may contain personal search terms.
- **Output sanitization**: recursively strips fields matching
  `cookie|session|token|authorization|x-csrf-token|(api|access|secret)_key`.

Rationale for the deny-list model: see `.claude/spec.md` §5.2 and
`.claude/deployment-log.md` (2026-04-24 entry).

## Tests

```bash
.venv/bin/python -m pytest -q
# 47 passed
```
