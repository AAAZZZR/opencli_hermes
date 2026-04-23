# Distributed OpenCLI Fleet — Development Spec

> A Hermes-orchestrated, multi-node OpenCLI collection system for personal use.
> Cloud brain dispatches scraping tasks to a fleet of home laptops over reverse WebSocket.

---

## 1. Project Overview

### 1.1 What We're Building

A personal AI agent system where:

- **One cloud VPS** runs a Hermes Agent as the "brain" — accepts natural language
  requests ("fetch latest Xiaohongshu posts about X"), plans, dispatches, and
  summarizes results.
- **A fleet of home laptops** (MacBook / Win+WSL2 / Ubuntu) each run a thin
  agent that executes `opencli` commands against their locally-logged-in
  Chrome sessions, then stream results back.
- **A thin MCP server** in the middle bridges Hermes ↔ existing
  [opencli-admin](https://github.com/xjh1994/opencli-admin) infrastructure, so
  Hermes can discover nodes, route commands, and retrieve results as MCP tools.

### 1.2 Why This Architecture

- **Login state stays at home** — sensitive cookies (Xiaohongshu, Weibo, X,
  Zhihu) never leave the laptop. VPS only sees normalized JSON output.
- **Cloud brain, edge hands** — the LLM (expensive, centralized) runs in one
  place; the browser automation (needs real login + residential IP) runs at
  the edge.
- **NAT-friendly** — laptops initiate outbound WSS to VPS, no port forwarding
  or dynamic DNS required.
- **Horizontally scalable** — adding a 6th laptop is one `curl | bash`.

### 1.3 Design Principles

1. **Reuse, don't rewrite.** opencli-admin already solved node registration,
   WS hub, dispatch, and admin UI. We add a thin MCP layer on top; we do NOT
   fork opencli-admin.
2. **Hermes knows nothing about nodes directly.** It only sees MCP tools.
   Swapping Hermes for Claude Code / another agent later changes nothing
   downstream.
3. **Security is non-negotiable.** Per-node tokens, command whitelist, audit
   log, rate limits. A compromised VPS must NOT be able to weaponize the
   laptops.
4. **Ship a usable MVP first.** Phase 1 proves the loop end-to-end. Phase 2+
   adds polish.

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
                                │ stdio MCP (same host)
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  VPS — fleet-mcp (our code, thin adapter)                           │
│   Exposes tools:                                                    │
│     • list_nodes()                                                  │
│     • list_supported_sites()                                        │
│     • dispatch(node_id, site, command, args)                        │
│     • dispatch_best(site, command, args)                            │
│     • broadcast(site, command, args)                                │
│     • get_task_status(task_id)                                      │
│   Calls opencli-admin REST API under the hood.                      │
│   Enforces: command whitelist, rate limit, audit log.               │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTP (localhost)
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  VPS — opencli-admin (upstream, unmodified or minimally patched)    │
│   - REST API  (:8031)                                               │
│   - Web UI    (:8030)  ← human ops console                          │
│   - WS Hub    (maintains reverse WSS to every laptop)               │
│   - Scheduler (cron-style recurring collections)                    │
│   - DB        (SQLite → Postgres later)                             │
└────────┬────────────┬────────────┬────────────┬────────────────────┘
         │ WSS        │ WSS        │ WSS        │ WSS
         │ reverse    │            │            │  (initiated by laptops)
  ┌──────▼─────┐ ┌────▼─────┐ ┌────▼─────┐ ┌────▼─────┐
  │ MacBook    │ │ Win/WSL2 │ │ Ubuntu   │ │ ...      │
  │ node-1     │ │ node-2   │ │ node-3   │ │          │
  │ opencli    │ │ opencli  │ │ opencli  │ │          │
  │ Chrome ✓   │ │ Chrome ✓ │ │ Chrome ✓ │ │          │
  │ logged in: │ │ logged:  │ │ logged:  │ │          │
  │  XHS, Zhihu│ │  Weibo,X │ │  Reddit  │ │          │
  └────────────┘ └──────────┘ └──────────┘ └──────────┘
```

### 2.1 Three Separate Deployments

| Role | Machine | How Deployed | What's Installed |
|------|---------|--------------|------------------|
| **Central** | 1 × VPS | `docker compose up` | Hermes + fleet-mcp + opencli-admin API/UI |
| **Node** | N × laptops | `curl ... \| bash` (script auto-generated by central) | agent_server.py + opencli + Chrome |
| **Brain** | Same VPS as Central | `hermes` CLI + config | Hermes Agent binary |

They are **fully independent installs**. The only link between them is that
each node knows the central's public URL and authenticates with a
per-node token.

### 2.2 Request Flow (End-to-End)

```
User asks Hermes: "check Zhihu hot list"
  ↓
Hermes LLM decides to call: dispatch_best(site="zhihu", command="hot")
  ↓
fleet-mcp receives MCP tool call
  ↓
fleet-mcp checks: is (zhihu, hot) in whitelist? is rate limit ok?
  ↓
fleet-mcp POSTs to http://localhost:8031/api/v1/collect (opencli-admin)
  ↓
opencli-admin picks a node (priority: manual > schedule > site-binding > free)
  ↓
opencli-admin pushes WS message to selected laptop:
  {"type":"collect", "request_id":"...", "site":"zhihu", "command":"hot"}
  ↓
Laptop's agent_server runs: opencli zhihu hot -f json
  ↓
opencli uses local Chrome (logged in) to scrape, returns JSON
  ↓
Laptop WS-sends result back to central
  ↓
Central resolves the Future, returns JSON to fleet-mcp
  ↓
fleet-mcp returns MCP tool result to Hermes
  ↓
Hermes summarizes with LLM → replies to user
```

Typical round-trip: 5–30 seconds depending on command.

---

## 3. Components

### 3.1 opencli-admin (Upstream Dependency)

- **Repo:** https://github.com/xjh1994/opencli-admin
- **Role:** Node management + WS hub + scheduler + web UI
- **Modification policy:** Pin to a specific tag; avoid forking if possible.
  If changes are required, maintain a minimal patch set in `patches/`.
- **Key endpoints we'll call from fleet-mcp:**
  - `GET /api/v1/nodes` — list nodes
  - `POST /api/v1/data-sources` — create/trigger collection
  - `POST /api/v1/tasks/trigger` — manual dispatch
  - `GET /api/v1/tasks/{id}` — poll task status
  - `GET /api/v1/records` — fetch collected records

### 3.2 fleet-mcp (Our Code, Primary Deliverable)

- **Language:** Python 3.11+
- **Framework:** [FastMCP](https://github.com/jlowin/fastmcp) v3+
- **Transport:** stdio (launched by Hermes as subprocess)
- **Lines of code target:** < 800 total
- **Repo structure:**

```
fleet-mcp/
├── pyproject.toml
├── README.md
├── .env.example
├── src/
│   └── fleet_mcp/
│       ├── __init__.py
│       ├── __main__.py          # entrypoint: python -m fleet_mcp
│       ├── server.py            # FastMCP server + tool definitions
│       ├── admin_client.py      # httpx client for opencli-admin REST API
│       ├── security.py          # command whitelist, rate limiter, audit log
│       ├── config.py            # pydantic-settings config loader
│       └── schemas.py           # pydantic models for tool inputs/outputs
└── tests/
    ├── test_admin_client.py
    ├── test_security.py
    └── test_server.py           # use FastMCP's in-memory test client
```

### 3.3 Node Agents

- **Source:** opencli-admin's `backend/agent_server.py` (unmodified for MVP)
- **Install:** Auto-generated shell script from `GET /api/v1/nodes/install/agent.sh`
- **Enhancement (Phase 2):** Add `detect_logged_in_sites()` at startup and
  report capabilities in the WS register message.

---

## 4. MCP Tool Specification

The core interface between Hermes and everything downstream.

### 4.1 `list_nodes()`

**Purpose:** Show Hermes which laptops are online and what sites they can
scrape.

**Input:** none

**Output:**
```json
{
  "nodes": [
    {
      "node_id": "alice-mbp",
      "label": "Alice's MacBook",
      "online": true,
      "last_seen": "2026-04-23T10:15:00Z",
      "logged_in_sites": ["xiaohongshu", "zhihu", "bilibili"],
      "chrome_mode": "cdp"
    }
  ]
}
```

### 4.2 `list_supported_sites()`

**Purpose:** Tell Hermes which (site, command) pairs are whitelisted.

**Input:** none

**Output:**
```json
{
  "sites": [
    {
      "site": "xiaohongshu",
      "commands": ["search", "note", "feed", "user"],
      "description": "Xiaohongshu (RedNote) — search and read posts"
    },
    {
      "site": "zhihu",
      "commands": ["hot", "search", "question"],
      "description": "Zhihu — Chinese Q&A platform"
    }
  ]
}
```

Built from `SUPPORTED_SITES` constant in `security.py`.

### 4.3 `dispatch(node_id, site, command, args)`

**Purpose:** Run an opencli command on a specific node.

**Input:**
```json
{
  "node_id": "alice-mbp",
  "site": "xiaohongshu",
  "command": "search",
  "args": {"q": "AI agents", "limit": 20},
  "format": "json"
}
```

**Output:**
```json
{
  "success": true,
  "node_id": "alice-mbp",
  "task_id": "uuid-...",
  "items": [ /* up to 50, truncated if more */ ],
  "truncated": false,
  "total_items": 20,
  "duration_ms": 4521
}
```

**Behavior:**
- Checks whitelist → returns error if `(site, command)` not allowed.
- Checks rate limit → returns error if exceeded.
- Writes audit log entry.
- Calls opencli-admin, awaits result, normalizes response.
- **Truncates items if > 50** to protect Hermes' context window. Full data
  stays in opencli-admin DB; client can query via `get_task_status`.

### 4.4 `dispatch_best(site, command, args)`

**Purpose:** Auto-select the best node for a site (one logged in to that site).

**Input:** same as `dispatch()` but no `node_id`.

**Output:** same as `dispatch()`.

**Node selection logic:**
1. Online nodes with `site` in their `logged_in_sites`
2. If multiple → least recently used
3. If none → return error suggesting which nodes need to log in

### 4.5 `broadcast(site, command, args)`

**Purpose:** Run on **all** nodes logged in to `site`, e.g. multi-account
data collection.

**Input:** same as `dispatch_best()`.

**Output:**
```json
{
  "total_nodes": 3,
  "results": [
    {"node_id": "alice-mbp", "success": true, "items": [...]},
    {"node_id": "bob-win",   "success": true, "items": [...]},
    {"node_id": "home-nuc",  "success": false, "error": "timeout"}
  ]
}
```

Uses `asyncio.gather` with per-node timeout. Does not fail the whole call if
one node errors.

### 4.6 `get_task_status(task_id)`

**Purpose:** Retrieve the full, untruncated result of a previous dispatch.

**Input:** `{"task_id": "uuid-..."}`

**Output:** full item array from opencli-admin DB.

---

## 5. Security

**These are not optional.** Ship all six in Phase 1.

### 5.1 Per-Node Tokens

- Each node gets a unique token (generated in admin DB when you add a node).
- Node sends `{"type":"register", "node_id":"...", "token":"..."}` on WS
  connect.
- Central verifies; rejects with WS close code 4001 if invalid.
- Revoking one node: delete its row in DB. Other nodes unaffected.

### 5.2 Command Whitelist

- Hardcoded in `fleet_mcp/security.py`:

```python
SUPPORTED_SITES: dict[str, set[str]] = {
    "xiaohongshu": {"search", "note", "feed", "user"},
    "zhihu":       {"hot", "search", "question"},
    "bilibili":    {"hot", "search", "ranking"},
    "weibo":       {"hot", "search"},
    "twitter":     {"search", "timeline", "profile"},
    "reddit":      {"hot", "subreddit", "search"},
    # add new ones here as you need them
}

# Explicitly forbidden regardless of site:
FORBIDDEN_COMMANDS: set[str] = {"browser", "eval", "register", "exec", "shell"}
```

- `browser.eval` can inject arbitrary JS — **never** expose it.
- `register` lets opencli run arbitrary local CLIs — **never** expose it.

### 5.3 Rate Limiting

- Token bucket per node: 10 requests/minute, burst 3.
- Global: 60 requests/minute.
- Implemented with `limits` library or simple in-memory counter.
- Exceeding returns error, doesn't queue. Hermes retries via its own logic.

### 5.4 Audit Log

- Every tool call logged to `~/.fleet-mcp/audit.log` (JSONL):

```json
{"ts":"2026-04-23T10:15:00Z","tool":"dispatch","node_id":"alice-mbp",
 "site":"xiaohongshu","command":"search","args_hash":"sha256:...",
 "result":"ok","duration_ms":4521,"items_count":20}
```

- Never log raw args (may contain personal search terms) — hash them.
- Rotate daily, keep 30 days.

### 5.5 Output Sanitization

- Before returning to Hermes, strip fields: `cookie`, `session`, `token`,
  `x-csrf-token`, `authorization`, any field matching `/(api|access|secret)_?key/i`.
- Implement as a recursive dict walker in `security.py`.

### 5.6 TLS Everywhere

- Central → public internet: Caddy auto-TLS with Let's Encrypt.
- Node → Central: `wss://`, reject plain `ws://` in config loader.
- fleet-mcp ↔ opencli-admin: localhost-only (`http://localhost:8031`), never
  exposed.

---

## 6. Configuration

### 6.1 fleet-mcp Config

`.env` file, loaded by pydantic-settings:

```bash
# opencli-admin backend (always localhost)
ADMIN_API_URL=http://localhost:8031
ADMIN_API_KEY=                    # if admin gets auth added

# Security
RATE_LIMIT_PER_NODE=10            # per minute
RATE_LIMIT_GLOBAL=60              # per minute
AUDIT_LOG_PATH=~/.fleet-mcp/audit.log
MAX_ITEMS_INLINE=50               # truncate above this

# Timeouts
TASK_TIMEOUT_SEC=120
BROADCAST_TIMEOUT_SEC=180

# Logging
LOG_LEVEL=INFO
```

### 6.2 Hermes Config

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  fleet:
    command: "python"
    args: ["-m", "fleet_mcp"]
    env:
      ADMIN_API_URL: "http://localhost:8031"
    tools:
      include:
        - list_nodes
        - list_supported_sites
        - dispatch
        - dispatch_best
        - broadcast
        - get_task_status
    connect_timeout: 30
    timeout: 180
```

---

## 7. Deployment

### 7.1 VPS Setup (one-time)

```bash
# 1. Install Docker, Caddy (for TLS), uv (for Python)
# Standard Ubuntu/Debian setup — not covered here

# 2. Clone opencli-admin
git clone https://github.com/xjh1994/opencli-admin.git
cd opencli-admin
cp .env.example .env

# Edit .env — critical changes:
#   COLLECTION_MODE=agent              (not "local")
#   PUBLIC_URL=https://fleet.yourdomain.com
#   SECRET_KEY=<openssl rand -hex 32>

# 3. Start central services (skip the built-in agent-1 — we don't need it)
docker compose up -d api frontend

# 4. Install Hermes
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
hermes setup  # pick your model provider, skip gateway for now

# 5. Install fleet-mcp
git clone https://github.com/you/fleet-mcp.git
cd fleet-mcp
uv pip install -e .
cp .env.example .env  # defaults are usually fine on VPS

# 6. Add fleet MCP server to Hermes config (see section 6.2)
vim ~/.hermes/config.yaml

# 7. Configure Caddy for TLS
cat > /etc/caddy/Caddyfile <<EOF
fleet.yourdomain.com {
    reverse_proxy localhost:8031
}
admin.yourdomain.com {
    reverse_proxy localhost:8030
    basicauth * {
        admin <bcrypt-hash>
    }
}
EOF
systemctl reload caddy

# 8. Test
hermes
> list my nodes
# → should return empty list (no nodes registered yet)
```

### 7.2 Adding a Node (repeat per laptop)

```bash
# On VPS: go to Web UI → Node Management → Add Node
# Copy the generated curl command (it has CENTRAL_API_URL pre-filled)

# On laptop (Mac / Win WSL2 / Ubuntu):
curl -fsSL https://fleet.yourdomain.com/api/v1/nodes/install/agent.sh \
  | AGENT_REGISTER=ws bash

# 驗證
# On VPS: Web UI → Node Management → should show new node with green dot
# Or via Hermes:
hermes
> list my nodes
# → should include the new one
```

### 7.3 Logging Into Sites (one-time per site per node)

```bash
# On laptop:
# If using Docker install (default):
docker exec -it opencli-agent bash
# Chrome is headless inside the container — use noVNC if you enabled it
# Or if you're using native install (not Docker), just open the Chrome window

# Navigate to the site, log in normally. Cookies persist in the profile.
# Examples:
#   https://www.xiaohongshu.com  (sms/qr login)
#   https://www.zhihu.com
#   https://twitter.com
```

### 7.4 Updating the Fleet

```bash
# Central (VPS):
cd opencli-admin && git pull && docker compose pull && docker compose up -d
cd ../fleet-mcp && git pull && uv pip install -e .

# Nodes (each laptop):
# Re-run the install script — it's idempotent and pulls the latest image.
curl -fsSL https://fleet.yourdomain.com/api/v1/nodes/install/agent.sh | bash
```

---

## 8. Development Roadmap

### Phase 1 — MVP (target: 1 week)

**Goal:** End-to-end loop working with 2 nodes.

- [ ] Scaffold `fleet-mcp` repo with FastMCP skeleton
- [ ] Implement `admin_client.py` — httpx wrapper over opencli-admin REST API
- [ ] Implement all 6 MCP tools (sections 4.1–4.6)
- [ ] Implement `security.py` — whitelist, rate limit, audit log, sanitization
- [ ] Write tests with FastMCP in-memory client + mocked admin
- [ ] Deploy to VPS, register 2 nodes (one Mac, one WSL2)
- [ ] Manually log in to Xiaohongshu + Zhihu on each
- [ ] Ask Hermes: "what are the hot topics on Zhihu?" → get real results

### Phase 2 — Production Polish (target: +1 week)

- [ ] Node capability auto-detection — agent reports `logged_in_sites` at
      WS handshake (requires minor patch to `agent_server.py`)
- [ ] Hermes skill file (`~/.hermes/skills/fleet.md`) with best-practice prompts
      for the tools
- [ ] Basic monitoring: fleet-mcp exposes `/health` and Prometheus metrics
- [ ] Systemd service files for fleet-mcp on VPS
- [ ] Runbook: what to do when a node goes offline, when Chrome session expires,
      when opencli-admin gets out-of-sync

### Phase 3 — Nice-to-Have (no target)

- [ ] Scheduled collections driven by Hermes cron (e.g. "every morning,
      check X, Y, Z and email me a summary")
- [ ] Multi-account support per site per node (different Chrome profiles)
- [ ] Auto-generation of new opencli adapters for sites not yet supported
      (via `opencli explore` + `synthesize`)
- [ ] Add non-opencli channels: arbitrary RSS, direct API, etc. (opencli-admin
      already supports these — just expose via MCP)

---

## 9. Testing Strategy

### 9.1 Unit Tests

Each module has isolated tests. Target: **80% line coverage on fleet-mcp**.

- `test_admin_client.py` — mock httpx, verify correct URL construction
- `test_security.py` — whitelist decisions, rate limit edge cases,
  sanitization with nested dicts
- `test_server.py` — use `fastmcp.Client` with in-memory transport:

```python
from fastmcp import Client
from fleet_mcp.server import mcp

async def test_dispatch_rejects_unknown_site():
    async with Client(mcp) as client:
        result = await client.call_tool("dispatch", {
            "node_id": "test",
            "site": "unknown_site",
            "command": "search",
            "args": {}
        })
        assert result["success"] is False
        assert "not allowed" in result["error"]
```

### 9.2 Integration Tests

`tests/integration/` — requires a running opencli-admin (Docker).

- `test_full_loop.py` — spin up admin in ephemeral container, register a
  mock WS agent, call fleet-mcp tools, verify round-trip.

### 9.3 Manual Smoke Test

After every deploy:

1. `hermes` → `list my nodes` → expect ≥ 2 online
2. `hermes` → `dispatch to <node> zhihu hot` → expect ≥ 10 items
3. Pull the plug on a node → re-run step 1 → expect that node offline within 60s
4. Check `~/.fleet-mcp/audit.log` — both calls logged, no tokens leaked

---

## 10. Open Questions / Decisions Needed

- [ ] **Which model for Hermes?** Claude Sonnet 4.6 via API? OpenRouter for
      cheaper fallback? Decide based on expected volume.
- [ ] **Authentication for opencli-admin REST API.** Currently no auth.
      Options: (a) bind to localhost only (simplest, assumes VPS is trusted),
      (b) add basic auth, (c) add API key middleware. Start with (a).
- [ ] **Where to store per-node tokens?** opencli-admin doesn't have this
      natively. Either: patch admin to add a `token` column on `EdgeNode`,
      or put it in fleet-mcp's own SQLite side-DB. Decide in Phase 1.
- [ ] **Chrome session expiry.** Xiaohongshu logs out after ~30 days.
      Need a runbook + possibly a `/diagnose` tool that detects this.
- [ ] **Logging destination.** Stick with local audit.log, or ship to
      Grafana Loki? Start local, revisit if fleet grows beyond 10 nodes.

---

## 11. References

- **opencli-admin** — https://github.com/xjh1994/opencli-admin
- **OpenCLI** — https://github.com/jackwener/OpenCLI
- **Hermes Agent docs** — https://hermes-agent.nousresearch.com/docs
- **Hermes MCP integration** — https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp
- **FastMCP** — https://github.com/jlowin/fastmcp
- **Model Context Protocol spec** — https://modelcontextprotocol.io

---

## 12. Glossary

| Term | Meaning |
|------|---------|
| **Central** | The VPS running Hermes + fleet-mcp + opencli-admin |
| **Node** | A laptop running `agent_server.py` + opencli + Chrome |
| **Brain** | Hermes Agent, the LLM-driven orchestrator |
| **fleet-mcp** | Our thin MCP server that bridges Hermes to opencli-admin |
| **Reverse WS** | WebSocket initiated by the node (outbound) to central, used because nodes are behind NAT |
| **Dispatch** | Sending a collect task from central to a specific node |
| **Bridge mode** | opencli talking to Chrome via the Browser Bridge extension |
| **CDP mode** | opencli talking to Chrome via Chrome DevTools Protocol directly |