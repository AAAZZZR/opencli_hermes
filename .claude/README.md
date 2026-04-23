# Orientation — read this first

You (Claude / any fresh reader) are looking at **`opencli_hermes`**, a
personal distributed scraping fleet:

> A Hermes Agent on a VPS dispatches `@jackwener/opencli` commands to a
> fleet of home laptops over reverse WebSocket. Cloud brain, edge hands.
> Login cookies stay at home; VPS only sees normalized JSON.

Three Python packages. All ours. No runtime dependency on `opencli-admin`
(evaluated and rejected — see `feature-audit.md`).

## Where each package lives / runs

```
          VPS                       Laptop (WSL2 / macOS / Linux)
   ┌──────────────────┐             ┌───────────────────────────┐
   │ Hermes  (brain)  │             │                           │
   │  ↓ stdio MCP     │             │                           │
   │ fleet-mcp        │             │ fleet-agent               │
   │  ↓ localhost:8031│ ←─ wss:// ─ │  ↓ subprocess             │
   │ fleet-hub        │             │ @jackwener/opencli        │
   │  ↓ SQLite        │             │  ↓ CDP/Bridge             │
   │ fleet_hub.db     │             │ Chrome (logged in)        │
   └──────────────────┘             └───────────────────────────┘
```

Hermes never sees nodes directly. It only calls 6 MCP tools exposed by
`fleet-mcp`. Swap Hermes for any MCP client (Claude Desktop, another agent
framework) and nothing downstream changes.

## Repo tree at a glance

```
opencli_agent/
├── .claude/                  ← You are here. Internal project docs.
│   ├── README.md             ← this file
│   ├── spec.md               ← full architecture spec + WS/REST contracts
│   ├── feature-audit.md      ← why we rewrote instead of using opencli-admin
│   └── deployment.md         ← real deployment runbook (with this project's IP + repo)
│
├── fleet-mcp/                ← MCP adapter; Hermes launches via stdio
│   └── src/fleet_mcp/
│       ├── server.py         ← 6 tools: list_nodes / dispatch / dispatch_best / ...
│       ├── hub_client.py     ← httpx wrapper over fleet-hub REST
│       ├── security.py       ← whitelist / rate limit / audit / sanitize
│       ├── schemas.py        ← pydantic models (HubNode, HubTaskResult, DispatchResult, ...)
│       └── config.py
│
├── fleet-hub/                ← VPS central: FastAPI + SQLite
│   └── src/fleet_hub/
│       ├── app.py            ← FastAPI app + lifespan
│       ├── models.py         ← ORM (Node, Task, Record)
│       ├── api/
│       │   ├── nodes.py      ← CRUD + WS endpoint /api/v1/nodes/ws
│       │   ├── tasks.py      ← POST /tasks → dispatch → pipeline → return
│       │   ├── install.py    ← serves the bash installer
│       │   └── health.py
│       ├── ws/manager.py     ← per-node connection + asyncio.Future dispatch
│       ├── pipeline/
│       │   ├── normalize.py  ← field-alias → {id,title,url,content,author,...}
│       │   └── store.py      ← sanitize → dedup by content_hash → insert
│       ├── security.py       ← token generation + output sanitization + audit
│       └── config.py
│
├── fleet-agent/              ← Laptop-side runner
│   └── src/fleet_agent/
│       ├── ws_client.py      ← connect/reconnect, register, dispatch handler
│       ├── runner.py         ← build argv → asyncio subprocess → map exit codes
│       ├── login_detect.py   ← probe logged-in sites at register time
│       └── config.py
│
├── deploy/
│   ├── README.md             ← generic deployment runbook (template)
│   └── vps/
│       ├── setup.sh          ← one-shot VPS installer
│       ├── fleet-hub.service ← systemd unit template
│       └── Caddyfile         ← TLS reverse proxy template (sslip.io)
│
├── docs/
│   └── hermes-config.yaml    ← snippet to merge into ~/.hermes/config.yaml
│
└── README.md                 ← monorepo overview for humans
```

## Reading order by question type

| Your question | Read |
|---------------|------|
| What does this project DO and why this architecture? | `spec.md` §1–2 |
| What's the REST / WS contract? | `spec.md` §4 (authoritative) |
| Why didn't we use `opencli-admin`? | `feature-audit.md` |
| How do I deploy this? | `deployment.md` (this project's values); `deploy/README.md` (template) |
| How does a dispatch flow end-to-end? | `spec.md` §2.1 + `fleet-hub/src/fleet_hub/api/tasks.py::_dispatch_and_persist` |
| What are the 6 MCP tools? | `fleet-mcp/src/fleet_mcp/server.py` (definitive) or `spec.md` §4.3 |
| What sites/commands are allowed? | `fleet-mcp/src/fleet_mcp/security.py::SUPPORTED_SITES` |
| What sensitive fields get stripped? | `fleet-hub/src/fleet_hub/security.py::_SENSITIVE_PATTERN` |
| How does the agent talk to opencli? | `fleet-agent/src/fleet_agent/runner.py` |
| What's the WS frame schema? | `fleet-hub/src/fleet_hub/schemas.py` + `spec.md` §4.2 |

## Project-level conventions (these override defaults)

- **No LOC cap.** Earlier spec revisions had size targets; they're void.
  Memory: `C:\Users\Owner\.claude\projects\C--Users-Owner-Desktop-opencli-agent\memory\feedback_no_loc_cap.md`
- **Rewrite over reuse.** Don't introduce `opencli-admin` as a dependency.
  If you find yourself saying "let's just vendor X from opencli-admin," stop and
  reconsider. Memory: `feedback_rewrite_over_reuse.md`.
- **繁體中文 for conversation**, **English for code/files/commits**. Conventional
  commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`). Commit body in
  Traditional Chinese is fine; commit type prefix stays English.
- **User confirms before commits.** Don't auto-commit unless explicitly asked.
  Don't auto-push unless explicitly asked.
- **Pin `@jackwener/opencli` to `@latest` by default.** Hub env var
  `OPENCLI_NPM_SPEC` controls what laptops install.

## How to run tests

Each package is independent. All three are quick (< 5s each).

```bash
cd fleet-hub   && .venv/Scripts/python -m pytest -q   # 35 tests
cd fleet-mcp   && .venv/Scripts/python -m pytest -q   # 44 tests
cd fleet-agent && .venv/Scripts/python -m pytest -q   # 24 tests
# Total: 103 green
```

On Linux/macOS replace `.venv/Scripts/python` with `.venv/bin/python`.

## Common edit tasks — where to start

| Task | Starting point |
|------|----------------|
| Allow a new (site, command) pair | `fleet-mcp/src/fleet_mcp/security.py::SUPPORTED_SITES` + probe recipe in `fleet-agent/src/fleet_agent/login_detect.py::_PROBES` |
| Add a new MCP tool | `fleet-mcp/src/fleet_mcp/server.py` + cover in `tests/test_server.py` |
| Change the WS frame schema | `fleet-hub/src/fleet_hub/schemas.py` (WS* classes) + matching change in `fleet-agent/src/fleet_agent/ws_client.py` |
| Add a new REST endpoint | `fleet-hub/src/fleet_hub/api/` (new router, register in `api/__init__.py`) |
| Change a normalization field alias | `fleet-hub/src/fleet_hub/pipeline/normalize.py` (`_TITLE_KEYS`, etc.) |
| Change an exit-code mapping | `fleet-agent/src/fleet_agent/runner.py::_EXIT_CODE_MAP` |
| Tweak the agent installer | `fleet-hub/scripts/install-agent.sh` (served by `install.py`) |

Whenever you change a cross-package contract (WS frame, REST schema,
`DispatchResult`), update **all three** packages and the `spec.md` §4
section in the same commit.

## Key invariants not to break

1. **Per-node tokens are the only auth.** `fleet-hub` validates tokens on
   WS handshake; without a valid token, the WS is closed (4001). If you
   add a new client path that can bypass this, the whole security model
   is broken.
2. **No sensitive-field leakage.** Both `fleet-hub` and `fleet-mcp`
   recursively sanitize items before storage/response. Don't bypass.
3. **`tasks` have exactly one in-flight WS per `task_id`**.
   `WSManager.dispatch` creates a `Future` keyed by `task_id`; result
   frames are resolved by `task_id` lookup. Don't reuse task IDs.
4. **The hub's SQLite DB is authoritative.** Hermes context window
   truncates; `get_task_status` always reads back from the DB.
5. **Installer gets the token from the URL.** `GET /api/v1/nodes/install/agent.sh?label=<label>`
   substitutes the node's token into the bash script at request time.
   Anyone who can hit this endpoint can download any node's token — don't
   expose the hub publicly without a reverse proxy + auth, or restrict
   the endpoint further.

## If you're stuck

Check memory:

```
C:\Users\Owner\.claude\projects\C--Users-Owner-Desktop-opencli-agent\memory\
  MEMORY.md
  project_architecture_pivot.md
  feedback_rewrite_over_reuse.md
  feedback_no_loc_cap.md
```

These hold decisions that led to the current state and aren't obvious
from reading the code alone.
