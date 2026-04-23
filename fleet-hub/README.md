# fleet-hub

Central hub for the OpenCLI fleet. Runs on the VPS. FastAPI + SQLite + WS
dispatcher + collection pipeline. **Not built on `opencli-admin`** — we
wrote this from scratch because most of opencli-admin's features (AI
processors, notification rules, browser pool, Web UI, Celery) are covered
by Hermes or aren't needed at all for personal use.

## Install

```bash
uv venv .venv && uv pip install -e ".[dev]"
cp .env.example .env
# Edit PUBLIC_URL to your https domain so node installers get the right URL.
```

## Run

```bash
.venv/bin/python -m fleet_hub
# default: 0.0.0.0:8031
```

The DB (`fleet_hub.db`) is created at startup via
`Base.metadata.create_all`. No Alembic yet — drop the file if you want a
clean slate.

## API

### Nodes

```
POST   /api/v1/nodes              {label}              → {id, label, token, ...}
GET    /api/v1/nodes              → [NodeOut, ...]
GET    /api/v1/nodes/{id|label}   → NodeOut
DELETE /api/v1/nodes/{id|label}   → 204
WS     /api/v1/nodes/ws           agent WS endpoint
GET    /api/v1/nodes/install/agent.sh?label=<label>
                                   → bash installer, with token baked in
```

Token is only returned at creation. Record it, or recreate the node.

### Tasks

```
POST   /api/v1/tasks              {node_id, site, command, args, ...}  → TaskResult
GET    /api/v1/tasks              ?node_id=&site=&status=&limit=       → [TaskOut]
GET    /api/v1/tasks/{id}                                              → TaskOut
GET    /api/v1/tasks/{id}/records ?limit=500                           → {items, total}
```

`POST /tasks` accepts `wait: true` (default) to synchronously dispatch via
WS, await the agent's result frame, store records, and return the full
`TaskResult` in one round-trip. With `wait: false` the hub returns
immediately and dispatches in the background; caller polls
`GET /tasks/{id}`.

### Health

```
GET /health → {"status": "ok", "version": "0.1.0"}
```

## WS protocol

See `.claude/spec.md` §4.2 in the monorepo root. Summary:

- Agent sends `{type:"register", token, mode, os, logged_in_sites, opencli_version}`
- Hub responds `{type:"registered", node_id, label}` or closes 4001/4002
- Hub dispatches `{type:"collect", task_id, site, command, args, positional_args, format, timeout}`
- Agent replies `{type:"result", task_id, success, items, exit_code, duration_ms, error?}`
- `ping`/`pong` keepalive on both sides

Invalid token → close 4001. Invalid register frame → close 4002. Reconnect
by the same node → previous WS closed with 4000.

## Pipeline

```
items from agent → sanitize → normalize (alias field names) →
  content_hash (sha256 of site|command|id|title|url|content) →
  dedup per-task → insert Records
```

Normalized keys: `id, title, url, content, author, published_at, extra`.
Everything else is preserved under `extra`.

## Install script

`GET /api/v1/nodes/install/agent.sh?label=<label>` reads
`scripts/install-agent.sh`, substitutes the hub's `PUBLIC_URL`, the node's
token, its label, `OPENCLI_NPM_SPEC`, and `FLEET_AGENT_INSTALL_SPEC`, then
returns it as `text/plain`. The laptop pipes it into bash.

Works on macOS (launchd) and Linux/WSL (systemd --user or nohup fallback).

## Tests

```bash
.venv/bin/python -m pytest -q
# 35 passed
```

Coverage:
- REST CRUD for nodes
- Task dispatch happy path (with stubbed WSManager)
- Dedup across batches
- Sanitization end-to-end
- NodeOffline / Timeout / AuthRequired error paths
- WSManager future plumbing (attach/detach/dispatch/resolve/cancel)
