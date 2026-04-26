# Codex Project Notes

Last updated: 2026-04-26

## What This Project Does

`opencli_agent` is a distributed OpenCLI fleet for personal automation and
scraping. A VPS runs the central services, while home laptops run agents that
execute `@jackwener/opencli` locally against their own logged-in Chrome
sessions. The design keeps login cookies on the laptops and sends only
normalized results back to the VPS.

The repo is a three-package Python monorepo:

- `fleet-hub`: FastAPI + SQLite central service on the VPS. It owns REST APIs,
  node registration, WebSocket dispatch, task persistence, record storage, and
  the installer endpoint.
- `fleet-agent`: Laptop-side WebSocket client. It registers with the hub,
  runs OpenCLI subprocesses, detects likely logged-in sites, and reports
  results.
- `fleet-mcp`: MCP adapter for Hermes or another MCP-compatible agent. It
  exposes fleet tools, applies command policy, rate limiting, audit logging,
  result sanitization, and calls `fleet-hub` over localhost HTTP.

The intended flow is:

1. Hermes calls a `fleet-mcp` tool such as `dispatch_best`.
2. `fleet-mcp` checks site/command policy and asks `fleet-hub` to create a task.
3. `fleet-hub` sends a WebSocket `collect` frame to an online agent.
4. `fleet-agent` runs `opencli <site> <command> ... --format json`.
5. The agent sends a result frame back.
6. `fleet-hub` normalizes, sanitizes, deduplicates, stores, and returns results.

Important project context lives in `.claude/`, especially:

- `.claude/README.md`: orientation and reading order.
- `.claude/spec.md`: architecture and contracts.
- `.claude/deployment.md`: live deployment runbook.
- `.claude/deployment-log.md`: real deployment history and gotchas.
- `.claude/develop/install-ticket.md`: planned safer one-time installer flow.

## Findings From Initial Audit

These were found by static review first, then confirmed with local tests and
remote checks where noted. Items 1-7 were fixed in the Codex audit branch on
2026-04-26; items 8-9 remain known maintenance tradeoffs.

1. Critical: the live public VPS exposed unauthenticated REST APIs.
   `deploy/vps/Caddyfile` only blocks `/api/v1/nodes/install/*`; all other
   paths are reverse-proxied to `fleet-hub`. Remote checks on
   `https://34.46.31.68.sslip.io` confirmed:

   - `GET /health` returns `200`.
   - `GET /api/v1/nodes` returns the node list.
   - `GET /api/v1/tasks` returns historical task metadata.
   - invalid `POST /api/v1/nodes` and `POST /api/v1/tasks` reach FastAPI and
     return `422`, proving the write routes are publicly reachable.
   - `GET /api/v1/nodes/install/agent.sh?label=home-wsl` correctly returns
     `403`.

   This bypassed `fleet-mcp` policy entirely. Since `fleet-hub` itself has no
   REST auth or command whitelist, public callers may be able to create nodes,
   inspect metadata, or dispatch arbitrary OpenCLI commands if an agent is
   online. Caddy should expose only `/health` and `/api/v1/nodes/ws` publicly,
   or REST should get real authentication. Fixed by narrowing Caddy exposure to
   public `/health` and `/api/v1/nodes/ws` only.

2. macOS installer contained a known broken path for Homebrew/npm installs.
   `fleet-hub/scripts/install-agent.sh` uses `opencli --version` and writes
   `OPENCLI_BIN=$(command -v opencli)` without a fallback. The generated
   launchd plist also lacks a `PATH`, so OpenCLI's `#!/usr/bin/env node`
   shebang can fail under launchd. `.claude/deployment-log.md` already records
   the real failure and suggested patch. Fixed by resolving `opencli` after
   npm install, adding a launchd/systemd/nohup `PATH`, and failing fast if the
   executable cannot be found.

3. Public installer instructions were stale in several places. The Caddyfile now
   blocks `/api/v1/nodes/install/*` publicly because rendered installer scripts
   contain node tokens, but `deploy/vps/setup.sh`, the installer header, and
   parts of top-level docs still mention `curl https://.../install/agent.sh |
   bash`. That command now returns 403 through the public proxy. Fixed in the
   deployment and package docs by documenting SSH localhost fetches instead.

4. `fleet-hub` defaulted to `HOST=0.0.0.0` in code, while the security model
   assumes REST is localhost-only and unauthenticated. The VPS setup script
   writes `HOST=127.0.0.1`, but directly running `python -m fleet_hub` without
   an env file could expose unauthenticated node/task APIs on all interfaces.
   Fixed by changing the default and `.env.example` to `127.0.0.1`.

5. `POST /api/v1/tasks` with `wait=false` had a commit race. In
   `fleet-hub/src/fleet_hub/api/tasks.py`, the route flushes a new task, then
   schedules `_dispatch_and_persist(task.id)` before the request-scoped session
   commits. The background session can run first, fail to find the task row,
   and raise `RuntimeError("task ... vanished")`. The synchronous `wait=true`
   path commits before dispatch and does not have this specific race. Fixed by
   committing before scheduling background dispatch and adding a regression
   test.

6. `fleet-hub` installer template loading was locale-sensitive on Windows.
   Local `fleet-hub` tests fail on Windows/cp950 because
   `api/install.py::_load_template()` reads `scripts/install-agent.sh` without
   `encoding="utf-8"`, while the script contains UTF-8 punctuation. Fixed by
   explicitly reading templates as UTF-8.

7. `fleet-agent` tests failed on native Windows because `os.killpg` does not
   exist. The runtime code says native Windows is unsupported, but the test
   fixture tries to monkeypatch `fleet_agent.runner.os.killpg` unconditionally.
   This makes local Windows development red even before runtime support is
   considered. Fixed by adding a process-kill fallback and portable tests.

8. Login detection is intentionally optimistic. `fleet-agent` treats timeout,
   service unavailable, empty, or unknown-probe cases as logged in. This avoids
   false negatives but can cause Hermes/fleet-mcp to pick a node that later
   fails with `AUTH_REQUIRED`, `SERVICE_UNAVAILABLE`, or timeout.

9. Several markdown docs have mojibake/encoding corruption in diagrams and some
   Chinese text. This does not affect runtime behavior but makes maintenance
   harder and should be cleaned when touching docs.

## Latest Verification Notes

- Branch created for Codex work: `codex/audit-20260426`.
- `fleet-hub`: `.venv/Scripts/python -m pytest -q` passes, `51 passed`.
- `fleet-agent`: `.venv/Scripts/python -m pytest -q` passes, `26 passed`.
- `fleet-mcp`: `.venv/Scripts/python -m pytest -q` passes, `54 passed`.
- VPS Caddy was updated from this branch and reloaded successfully. Public
  verification now shows `/health` returns `200`, while `/api/v1/nodes`,
  `/api/v1/tasks`, and `/api/v1/nodes/install/agent.sh?...` all return `403`.
- Public WebSocket routing remains open to the hub as intended. A test
  connection to `wss://34.46.31.68.sslip.io/api/v1/nodes/ws` with an invalid
  token reached the hub and was closed with `4001 invalid_token`.
- After the Caddy lock-down, the remote VPS `fleet-mcp` venv still works with
  `HUB_URL=http://localhost:8031`: `list_nodes` returns `home-wsl`, and
  `list_supported_sites` returns successfully.
- SSH access from this Windows workspace is available through
  `~/.ssh/id_ed25519_claude_code_macos` with fingerprint
  `SHA256:sTruhK4i/caWjplWpo6yB3E9S/ja0PvGMCrjHdXbBis`. This is an access
  credential only; do not bake it into code or deployment scripts.
- The pasted private key should be treated as exposed. Rotate or remove it from
  the VPS `authorized_keys` when a replacement access path exists.
- Local `fleet-mcp` was pointed at the remote hub with
  `HUB_URL=https://34.46.31.68.sslip.io` and exercised through FastMCP's
  in-memory client. `list_nodes` returned the remote `home-wsl` node as
  offline, and `list_supported_sites` returned 101 sites with `web.allowed`
  equal to `["read"]`.
- The remote VPS `fleet-mcp` venv was also tested over SSH without writing
  remote files. From `/opt/opencli_agent/fleet-mcp`, running its venv Python
  with `HUB_URL=http://localhost:8031` and `PYTHONPATH=src` could call
  `list_nodes` and `list_supported_sites` through FastMCP's in-memory client.
  Result: `home-wsl` is visible and offline; site count is 101; `web.allowed`
  is `["read"]`.

## Safety Notes For Future Codex Work

- Do not introduce `opencli-admin` as a dependency; the project deliberately
  rewrote the needed pieces.
- Treat per-node tokens as the only agent authentication boundary.
- Keep the public installer endpoint blocked unless a one-time ticket flow is
  implemented.
- If changing WebSocket, REST, or task result schemas, update all three
  packages together and then update `.claude/spec.md`.
- Preserve output sanitization in both `fleet-hub` and `fleet-mcp`.
- Do not auto-commit. The project convention says the user confirms commits.
