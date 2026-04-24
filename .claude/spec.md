# Distributed OpenCLI Fleet — Spec (rev. 2026-04-23)

> Hermes-orchestrated, multi-node OpenCLI collection system for personal use.
> Cloud brain dispatches scraping tasks to a fleet of home laptops over
> reverse WebSocket.
>
> **Status:** Phase 1 implementation complete. All three packages
> (fleet-mcp, fleet-hub, fleet-agent) live in this monorepo. 103 tests
> passing.

---

## 1. Overview

A personal AI agent system where:

- **One cloud VPS** runs a Hermes Agent as the "brain" — accepts natural
  language requests ("fetch latest Xiaohongshu posts about X"), plans,
  dispatches, summarizes.
- **A fleet of home laptops** (macOS / Linux / WSL2) each run `fleet-agent`,
  a thin Python process that executes `@jackwener/opencli` commands against
  their locally-logged-in Chrome sessions, then streams results back over a
  reverse WebSocket.
- **`fleet-mcp`** is the MCP adapter Hermes loads as a tool provider. It
  exposes 6 tools and forwards them to `fleet-hub` over localhost HTTP.
- **`fleet-hub`** is the VPS-side central. FastAPI + SQLite + WS hub +
  collection pipeline. **It is NOT a fork of `opencli-admin`** — we wrote
  it from scratch to drop the features Hermes already covers.

### Why this architecture

- **Login state stays at home** — sensitive cookies (Xiaohongshu, Weibo, X,
  Zhihu) never leave the laptop. VPS only sees normalized JSON output.
- **Cloud brain, edge hands** — the LLM runs in one place; the browser
  automation runs at the edge, where residential IPs and real logins are.
- **NAT-friendly** — laptops initiate outbound WSS to VPS, no port
  forwarding or dynamic DNS required.
- **Per-node tokens** — each laptop gets a unique registration token; the
  hub refuses WS connections without one.

### Design principles

1. **Replace, don't reuse.** We evaluated `xjh1994/opencli-admin` and
   decided to rewrite. Hermes handles the AI / notification / provider
   layers that `opencli-admin` includes, and we don't need its browser pool,
   Web UI, or Celery. Our rewrite is smaller and shaped to our contract.
2. **Hermes knows nothing about nodes directly.** It only sees MCP tools.
   Swapping Hermes for another LLM agent later changes nothing downstream.
3. **Ship usable first.** Phase 1 proves the end-to-end loop.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  User (Telegram / Web / Hermes CLI)                                 │
│  "summarize recent Xiaohongshu posts about topic X"                 │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ Hermes Gateway
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  VPS — Hermes Agent (brain)                                         │
│   - interprets intent, plans, summarizes                            │
│   - calls MCP tools on fleet-mcp                                    │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ stdio MCP
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  VPS — fleet-mcp (src/fleet_mcp)                                    │
│   6 tools: list_nodes, list_supported_sites,                        │
│            dispatch, dispatch_best, broadcast, get_task_status      │
│   Enforces: whitelist, rate limit, audit log, output sanitization   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTP (localhost :8031)
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  VPS — fleet-hub (src/fleet_hub)                                    │
│   REST API:  POST /tasks, GET /nodes, GET /tasks/{id}/records …    │
│   WS hub:    /api/v1/nodes/ws  (per-node token auth)                │
│   DB:        SQLite (nodes / tasks / records), Record dedup         │
│   Pipeline:  collect → normalize → store                            │
└────────┬────────────┬────────────┬────────────┬────────────────────┘
         │ WSS        │ WSS        │ WSS        │ WSS
         │ reverse    │            │            │  (initiated by laptops)
  ┌──────▼─────┐ ┌────▼─────┐ ┌────▼─────┐ ┌────▼─────┐
  │ MacBook    │ │ Linux    │ │ WSL2     │ │ ...      │
  │ fleet-agent│ │fleet-agen│ │fleet-agen│ │          │
  │ opencli    │ │ opencli  │ │ opencli  │ │          │
  │ Chrome ✓   │ │ Chrome ✓ │ │ Chrome ✓ │ │          │
  └────────────┘ └──────────┘ └──────────┘ └──────────┘
```

### 2.1 Request flow (end-to-end)

```
User asks Hermes: "check Zhihu hot list"
  ↓
Hermes LLM calls MCP tool: dispatch_best(site="zhihu", command="hot")
  ↓
fleet-mcp checks whitelist + rate limit + audit-logs the call
  ↓
fleet-mcp: GET /nodes → picks an online node that's logged in to zhihu (LRU)
  ↓
fleet-mcp: POST /tasks {node_id:"alice", site:"zhihu", command:"hot", wait:true}
  ↓
fleet-hub creates task row, sends WS frame to the node, awaits result
  ↓
fleet-agent runs: opencli zhihu hot --format json
  ↓
opencli uses local Chrome (logged in) to scrape, returns JSON
  ↓
fleet-agent sends {type:"result", items:[...], success:true} back over WS
  ↓
fleet-hub's pipeline normalizes → dedups → stores records, returns TaskResult
  ↓
fleet-mcp sanitizes (strips cookie/session fields), truncates to 50 items,
  returns DispatchResult to Hermes
  ↓
Hermes summarizes with LLM → replies to user
```

Typical round-trip: 5–30 seconds depending on command.

---

## 3. Components (monorepo layout)

```
opencli_agent/
├── fleet-mcp/           # MCP adapter for Hermes (Python)
├── fleet-hub/           # VPS central (FastAPI + SQLite)
├── fleet-agent/         # Laptop-side runner (Python WS client + subprocess)
├── docs/                # Hermes config examples, deployment notes
└── .claude/             # spec.md (this file) + feature-audit.md
```

### 3.1 fleet-mcp

- **Language:** Python 3.11+
- **Framework:** FastMCP v3+
- **Transport:** stdio (launched by Hermes as subprocess)
- **Enforces:** command whitelist, per-node + global rate limit, audit log,
  output sanitization, item-count truncation
- **Key files:** `src/fleet_mcp/{server.py, hub_client.py, security.py, schemas.py, config.py}`

### 3.2 fleet-hub

- **Language:** Python 3.11+
- **Framework:** FastAPI + SQLAlchemy 2.0 (async) + aiosqlite
- **Data model:** Node, Task, Record (see `src/fleet_hub/models.py`)
- **WS protocol:** documented in §4.2
- **REST endpoints:** documented in §4.1
- **Pipeline:** `src/fleet_hub/pipeline/{normalize,store}.py` — field-alias
  normalization, content-hash dedup per-task, recursive sanitization of
  sensitive fields

### 3.3 fleet-agent

- **Language:** Python 3.11+
- **Install:** via curl-piped installer served by fleet-hub (see §7.2)
- **Runs:** `@jackwener/opencli` (pinned by hub config to a npm spec —
  currently `@jackwener/opencli@latest`; pin to a version in production)
- **Auto-reconnects** with exponential backoff (default 3s → 60s)
- **Detects logged-in sites** at handshake time via cheap probes
  (`opencli <site> hot --limit 1 --format json`)

---

## 4. Interfaces

### 4.1 fleet-hub REST API (used by fleet-mcp)

Base path: `/api/v1`. No auth in front of REST — fleet-mcp only talks to it
over `http://localhost:8031` on the VPS. Expose through a reverse proxy
with auth if you ever publish it.

```
GET    /health                      → {status, version}
GET    /api/v1/nodes                → [NodeOut, ...]
POST   /api/v1/nodes                → {NodeCreated with token}
GET    /api/v1/nodes/{id|label}     → NodeOut
DELETE /api/v1/nodes/{id|label}     → 204
WS     /api/v1/nodes/ws             → agent endpoint (see §4.2)
GET    /api/v1/nodes/install/agent.sh?label=<label>
                                    → rendered bash installer

POST   /api/v1/tasks                → TaskResult
GET    /api/v1/tasks                → [TaskOut, ...]  (?node_id=, ?site=, ?status=)
GET    /api/v1/tasks/{id}           → TaskOut
GET    /api/v1/tasks/{id}/records   → {items:[...], total}
```

`POST /tasks` is the primary dispatch call:

```json
request:
{
  "node_id": "alice-mbp",       // label or UUID
  "site": "zhihu",
  "command": "hot",
  "args": {"limit": 10},
  "positional_args": [],
  "format": "json",
  "timeout_sec": 120,
  "wait": true
}

response (wait=true, success):
{
  "id": "<uuid>",
  "node_id": "<node uuid>",
  "site": "zhihu", "command": "hot", ...
  "status": "completed",
  "items_total": 10, "items_stored": 10,
  "duration_ms": 4521,
  "items": [{...}, ...]
}

response (wait=true, failure):
{
  ...
  "status": "failed",
  "error_code": "AUTH_REQUIRED",
  "error_message": "Zhihu logged out",
  "exit_code": 77,
  "items": []
}
```

### 4.2 WebSocket protocol (hub ↔ agent)

Endpoint: `wss://<central>/api/v1/nodes/ws`.

**Handshake** — agent sends first:

```json
{
  "type": "register",
  "token": "<node token>",
  "mode": "bridge",              // bridge | cdp
  "os": "darwin",                // darwin | linux | win
  "logged_in_sites": ["zhihu", "xiaohongshu"],
  "opencli_version": "1.7.7"
}
```

Hub validates the token against the DB. On success:

```json
{"type": "registered", "node_id": "<uuid>", "label": "<label>"}
```

Invalid token → WS close with code 4001.
Invalid frame → WS close with code 4002.
Connection replaced by a newer one → old closed with code 4000.

**Dispatch** — hub → agent:

```json
{
  "type": "collect",
  "task_id": "<uuid>",
  "site": "zhihu",
  "command": "hot",
  "args": {"limit": 10},
  "positional_args": [],
  "format": "json",
  "timeout": 120
}
```

**Result** — agent → hub:

```json
{
  "type": "result",
  "task_id": "<uuid>",
  "success": true,
  "items": [{...}, ...],
  "exit_code": 0,
  "duration_ms": 4521
}
```

On failure:

```json
{
  "type": "result",
  "task_id": "<uuid>",
  "success": false,
  "items": [],
  "error": {
    "code": "AUTH_REQUIRED",
    "message": "Zhihu logged out",
    "exit_code": 77,
    "stderr": "..."
  }
}
```

`exit_code` taxonomy follows OpenCLI's `src/errors.ts`:
`0 ok / 1 generic / 2 usage / 66 empty / 69 service / 75 timeout / 77 auth / 78 config`.

**Keepalive** — either side can send `{"type": "ping"}` at any time; the
other replies `{"type": "pong"}`. The underlying websockets library also
runs its own ping frames (`ping_interval=30s`, `ping_timeout=10s` by
default — configurable via agent env).

### 4.3 MCP tool specification (Hermes-facing)

All 6 tools live in `fleet_mcp/server.py`. Brief summary — see that file
for the full signatures:

| Tool | Purpose |
|------|---------|
| `list_nodes()` | Which laptops are online, what sites each is logged into |
| `list_supported_sites()` | Whitelisted (site, command) pairs |
| `dispatch(node_id, site, command, args, positional_args)` | Run on a specific node |
| `dispatch_best(site, command, args, positional_args)` | Auto-pick an LRU online node logged in to the site |
| `broadcast(site, command, args, positional_args)` | Fan-out to every online node logged in to the site |
| `get_task_status(task_id)` | Retrieve full untruncated records for a prior task |

Items are truncated to `MAX_ITEMS_INLINE=50` in tool responses to protect
Hermes' context. Full records are always retrievable via `get_task_status`.

---

## 5. Security

### 5.1 Per-node tokens (implemented)

- Every node has a `token` column on the `nodes` table, generated via
  `secrets.token_urlsafe(32)` when the admin calls `POST /api/v1/nodes`.
- Token is included in the WS register frame; hub validates by DB lookup.
- Revoking a node: `DELETE /api/v1/nodes/{label}`. The active WS is
  force-closed and the row is removed — other nodes unaffected.

### 5.2 Command whitelist (fleet-mcp only) — deny-list model

Defined in `fleet_mcp/security.py`. Model flipped from allow-list to deny-list
on 2026-04-24 (see `deployment-log.md`): the goal is "Hermes orchestrates every
opencli capability, except writes on your account". Shape:

```python
# Every opencli site fleet-mcp will dispatch to — 101 sites, derived from
# `opencli --help` minus framework verbs and external-CLI passthroughs.
SUPPORTED_SITES = frozenset({"1688","36kr",...,"zhihu","zsxq"})

# Framework-level bans (always blocked, regardless of site)
FORBIDDEN_GLOBAL = {
    "browser", "eval", "register", "install", "plugin",
    "daemon", "adapter", "synthesize", "record", "exec", "shell",
}

# Per-site write/mutation sub-commands (LLM must never run these on the
# user's account). Sites not in this dict have no blocked sub-commands.
FORBIDDEN_PER_SITE = {
    "reddit":   {"comment", "save", "subscribe", "upvote"},
    "zhihu":    {"answer", "comment", "favorite", "follow", "like"},
    "twitter":  {"post","reply","like","follow","delete","block",...},
    "instagram":{"post","comment","like","follow","save","story","reel",...},
    # ...37 sites total that have at least one write verb
}
```

`check_whitelist(site, cmd)` returns `None` iff:
1. `site ∈ SUPPORTED_SITES`, and
2. `cmd ∉ FORBIDDEN_GLOBAL`, and
3. `cmd ∉ FORBIDDEN_PER_SITE.get(site, ∅)`.

Otherwise it returns an error message describing which rule rejected the call.
Unknown sub-commands are NOT pre-validated (opencli rejects them downstream);
this avoids fleet-mcp maintaining the full per-site sub-command catalogue.

**Framework bans (global):**
- `browser`, `eval` — JS injection / page mutation
- `register`, `install`, `plugin` — install arbitrary CLIs / npm packages
- `daemon`, `adapter`, `synthesize`, `record`, `exec`, `shell` — write/mutate
  adapter state, execute arbitrary shell

**Per-site write bans (sample categories):**
- **Social writes:** `post`, `reply`, `comment`, `like`/`unlike`, `follow`/`unfollow`,
  `subscribe`/`unsubscribe`, `upvote`/`downvote`, `save`/`unsave`,
  `bookmark`/`unbookmark`, `block`/`unblock`, `delete`, `publish`, `repost`
- **AI chat writes:** `ask`, `send`, `new`, `model`, `image` (quota-consuming) —
  applied to chatgpt, chatgpt-app, chatwise, codex, cursor, deepseek, doubao,
  doubao-app, gemini, grok, yuanbao, antigravity
- **E-commerce:** `add-cart` / `add-to-cart` (coupang, jd, taobao)
- **Recruiter (boss):** `batchgreet`, `greet`, `invite`, `exchange`, `mark`, `send`
- **Cloud drive (quark):** `mkdir`, `mv`, `rename`, `rm`, `save`
- **Spotify:** all playback + auth (`play`, `pause`, `next`, `prev`, `queue`,
  `volume`, `shuffle`, `repeat`, `auth`)
- **v2ex:** `daily` (account sign-in & coin claim)
- **AI image gen:** `yollomi` all `generate`-type, `jimeng generate`/`new`

Full list lives in `fleet_mcp/security.py::FORBIDDEN_PER_SITE`. Adding a new
site: append to `SUPPORTED_SITES`, add its description, add its writes (if
any) to `FORBIDDEN_PER_SITE`. No code change to `check_whitelist` needed.

### 5.3 Rate limiting (fleet-mcp)

Token bucket, in-memory:
- Per-node: 10/min, burst 3
- Global: 60/min, burst `max(3, rpm/10)`

Exceeding returns an error; does not queue. Hermes retries via its own
logic.

### 5.4 Audit log (fleet-mcp + fleet-hub)

fleet-mcp: `~/.fleet-mcp/audit.log` (JSONL). Every tool call, with hashed
args (not raw — may contain search terms).

fleet-hub: `~/.fleet-hub/audit.log`. WS connects/disconnects, node
create/delete, task create/complete/fail.

### 5.5 Output sanitization

Both fleet-mcp and fleet-hub strip fields whose names match
`cookie|session|token|x-csrf-token|authorization|(api|access|secret)_key`
recursively before anything touches durable storage or crosses to Hermes.

### 5.6 TLS

- Central ↔ public internet: put Caddy in front of fleet-hub, auto-TLS
  via Let's Encrypt. Configure `PUBLIC_URL` to the `https://` endpoint so
  the install script tells nodes `wss://`.
- Node ↔ central: agent derives `wss://` or `ws://` from the
  `CENTRAL_URL` — use `https://` in production.
- fleet-mcp ↔ fleet-hub: localhost-only, `http://`.

---

## 6. Configuration

### 6.1 fleet-mcp (`.env`)

```bash
HUB_URL=http://localhost:8031
RATE_LIMIT_PER_NODE=10
RATE_LIMIT_GLOBAL=60
AUDIT_LOG_PATH=~/.fleet-mcp/audit.log
MAX_ITEMS_INLINE=50
TASK_TIMEOUT_SEC=120
BROADCAST_TIMEOUT_SEC=180
LOG_LEVEL=INFO
```

### 6.2 fleet-hub (`.env`)

```bash
HOST=0.0.0.0
PORT=8031

DATABASE_URL=sqlite+aiosqlite:///./fleet_hub.db
NODE_TOKEN_BYTES=32
AUDIT_LOG_PATH=~/.fleet-hub/audit.log

DEFAULT_TASK_TIMEOUT_SEC=120
MAX_TASK_TIMEOUT_SEC=600
WS_PING_INTERVAL_SEC=30
WS_PING_TIMEOUT_SEC=10
NODE_OFFLINE_AFTER_SEC=60

PUBLIC_URL=https://fleet.yourdomain.com
OPENCLI_NPM_SPEC=@jackwener/opencli@latest
FLEET_AGENT_INSTALL_SPEC=git+https://github.com/YOUR_ORG/opencli_agent.git#subdirectory=fleet-agent

LOG_LEVEL=INFO
```

### 6.3 fleet-agent (`~/.fleet-agent/config.env`, written by installer)

```bash
CENTRAL_URL=https://fleet.yourdomain.com
NODE_TOKEN=<generated at POST /api/v1/nodes>
NODE_LABEL=alice-mbp
OPENCLI_BIN=/usr/local/bin/opencli
AGENT_MODE=bridge
LOG_LEVEL=INFO
```

### 6.4 Hermes (`~/.hermes/config.yaml`)

See `docs/hermes-config.yaml` in the repo.

---

## 7. Deployment

The ready-to-run assets live in `deploy/`:

- `deploy/vps/setup.sh` — one-shot VPS installer (apt-installs Caddy, builds venvs, writes `.env`, installs systemd unit, configures Caddy + sslip.io TLS, waits for health).
- `deploy/vps/fleet-hub.service` — systemd unit template.
- `deploy/vps/Caddyfile` — TLS reverse proxy template (uses sslip.io so you don't need a domain).
- `fleet-hub/scripts/install-agent.sh` — bash installer served by `GET /api/v1/nodes/install/agent.sh`. Installs opencli (via npm), creates venv, `pip install`s fleet-agent, writes config, installs systemd `--user` (Linux/WSL) or launchd (macOS), starts service.

### 7.1 Template runbook → see `deploy/README.md`

Generic instructions suitable for any fork. Uses `YOUR_ORG`, `<VPS_IP>`,
`<label>` placeholders throughout. Handles troubleshooting for TLS
issuance, WSL2 + systemd gotcha, agent re-registration.

### 7.2 This project's runbook → see `.claude/deployment.md`

Real values baked in (`AAAZZZR/opencli_hermes`, VPS IP `34.46.31.68`,
hostname `34.46.31.68.sslip.io`, laptop on WSL2). This is the one to
follow verbatim for this setup.

### 7.3 Hermes MCP config → see `docs/hermes-config.yaml`

The snippet merged into `~/.hermes/config.yaml`. Uses absolute path to
fleet-mcp's venv python plus `PYTHONPATH` in `env:` — Hermes's stdio MCP
transport does NOT support a `cwd:` field (verified against upstream
`tools/mcp_tool.py`), and its subprocess env is allowlist-filtered.

### 7.4 Logging into sites (per site per node)

Normal Chrome login on the laptop (xiaohongshu, zhihu, twitter, etc.).
opencli's Bridge mode reuses the user's Chrome profile, so cookies persist
across agent restarts. At each fleet-agent register frame,
`logged_in_sites` is auto-detected via cheap probes (1 result per site).

---

## 8. Development roadmap

### Phase 1 — MVP  ✓ DONE (this rev)

- [x] Scaffold fleet-mcp, fleet-hub, fleet-agent
- [x] Hub REST + WS + pipeline
- [x] Agent WS client + subprocess runner + login probe
- [x] fleet-mcp with 6 tools, whitelist, rate limit, audit, sanitize
- [x] Full test suites (103 tests green)
- [x] Install script for agent deployment
- [ ] End-to-end smoke test on real hardware (2+ laptops + VPS)

### Phase 2 — Production polish

- [ ] Systemd units for fleet-hub and fleet-mcp on VPS (docs only needed)
- [ ] Alembic migrations (currently: `Base.metadata.create_all` at startup)
- [ ] Hub: webhook trigger for external systems (e.g. Hermes cron)
- [ ] Better per-site auth-check — propose `opencli auth-check <site>`
      upstream to avoid running real commands just to detect logout
- [ ] Runbook: Chrome session expiry, opencli version bumps, node
      offline > N hours

### Phase 3 — Nice-to-have

- [ ] Scheduled collections (cron-like) — add a scheduler to fleet-hub
- [ ] Multi-account per site per node (different Chrome profiles)
- [ ] Non-opencli channels: RSS, direct API (follow opencli-admin's
      channel registry pattern)
- [ ] Web UI (if CLI via Hermes ever isn't enough)

---

## 9. Testing strategy

Each package has its own pytest suite; all run in < 5s on 2026 hardware.

- **fleet-mcp** (44 tests): whitelist, rate limit, audit, sanitizer,
  hub_client (respx-mocked HTTP), all 6 MCP tools (FastMCP in-memory
  client), LRU tie-break behavior
- **fleet-hub** (35 tests): REST CRUD for nodes, task dispatch with a
  stubbed WSManager, pipeline unit tests (normalize/hash/store/dedup), WS
  manager future plumbing
- **fleet-agent** (24 tests): `build_argv` edge cases, subprocess runner
  with a fake `asyncio.Process`, error envelope preference, timeout kill,
  login probe logic, **end-to-end WS roundtrip** with an in-process fake
  hub (websockets.serve)

Manual smoke test after any deploy:

1. `hermes` → "list my nodes" → expect ≥ 1 online
2. `hermes` → "fetch zhihu hot list" → expect ≥ 10 items
3. Pull the plug on a node → re-run step 1 → node offline within 60s
4. Check `~/.fleet-hub/audit.log` — both calls logged

---

## 10. Decisions made, open questions

| Question | Decision / status |
|----------|-------------------|
| Reuse `opencli-admin` or rewrite? | **Rewrite** — smaller, simpler, ours. See feature-audit.md. |
| Per-node tokens — where stored? | First-class column on `nodes` table in fleet-hub DB. |
| REST auth on fleet-hub? | localhost-only from fleet-mcp, no REST auth; reverse proxy with basic auth if you ever expose it. |
| Bridge vs CDP mode? | Default `bridge` (opencli's default; handles extension lifecycle). Settable in agent config. |
| OpenCLI version pin? | **`@jackwener/opencli@latest`** for personal use. Pin to a specific version in production-ish setups. Updated via re-running the installer. |
| LOC cap? | **No cap.** The spec's old "< 800 LOC" targets are void. |
| Schema migrations? | None yet (sqlite + create_all). Add Alembic if schema starts drifting. |
| Chrome session expiry runbook? | Future work. |

---

## 11. References

- **OpenCLI (upstream CLI)** — https://github.com/jackwener/OpenCLI (Apache-2.0)
- **opencli-admin (not used)** — https://github.com/xjh1994/opencli-admin
  (evaluated in feature-audit.md; decided to rewrite)
- **Hermes Agent** — https://hermes-agent.nousresearch.com/docs
- **FastMCP** — https://github.com/jlowin/fastmcp
- **Model Context Protocol spec** — https://modelcontextprotocol.io

---

## 12. Glossary

| Term | Meaning |
|------|---------|
| **Central / hub** | The VPS running Hermes + fleet-mcp + fleet-hub |
| **Node** | A laptop running `fleet-agent` + `opencli` + Chrome |
| **Brain** | Hermes Agent, the LLM-driven orchestrator |
| **fleet-mcp** | Our MCP adapter; exposes 6 tools to Hermes |
| **fleet-hub** | Our central: REST + WS + pipeline (replaces opencli-admin) |
| **fleet-agent** | Our laptop process: WS client + subprocess runner |
| **Reverse WS** | WebSocket initiated by the node (outbound) to the central |
| **Bridge mode** | opencli talking to Chrome via its Browser Bridge extension + local daemon (default) |
| **CDP mode** | opencli talking to Chrome/Electron via Chrome DevTools Protocol |
