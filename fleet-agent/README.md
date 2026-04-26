# fleet-agent

Laptop-side process that connects to `fleet-hub` over WebSocket and runs
`@jackwener/opencli` commands on demand.

## Install (recommended: via the hub's installer)

```bash
ssh <vps-user>@<vps-host> \
  'curl -s "http://localhost:8031/api/v1/nodes/install/agent.sh?label=alice-mbp"' \
  | bash
```

The installer endpoint returns a script containing the node token, so the
public reverse proxy should block it. Fetch it through SSH/localhost on the VPS.

The installer:
- Verifies Python >= 3.11 and Node >= 21
- `npm install -g @jackwener/opencli@<spec-from-hub>`
- Creates a venv at `~/.fleet-agent/venv`
- `pip install`s fleet-agent from `FLEET_AGENT_INSTALL_SPEC` (configurable
  on the hub, typically a git URL)
- Writes `~/.fleet-agent/config.env` with the token + central URL
- Installs a launchd plist (macOS) or `systemd --user` unit (Linux/WSL)
  and starts the service

## Install (manual / dev)

```bash
uv venv .venv && uv pip install -e ".[dev]"
cp .env.example .env
# Edit CENTRAL_URL, NODE_TOKEN, NODE_LABEL
.venv/bin/python -m fleet_agent
```

## Config

Read from `$FLEET_AGENT_CONFIG` if set, else `~/.fleet-agent/config.env`,
else `./.env`.

| Var | Default |
|-----|---------|
| `CENTRAL_URL` | — (required) |
| `NODE_TOKEN` | — (required) |
| `NODE_LABEL` | — (required, advisory) |
| `OPENCLI_BIN` | `opencli` |
| `AGENT_MODE` | `bridge` |
| `WS_RECONNECT_MIN_SEC` / `WS_RECONNECT_MAX_SEC` | `3` / `60` |
| `WS_PING_INTERVAL_SEC` / `WS_PING_TIMEOUT_SEC` | `30` / `10` |
| `LOGIN_PROBE_TIMEOUT_SEC` | `10` |
| `LOG_LEVEL` | `INFO` |

## What it does

On startup:
1. Detects opencli version (`opencli --version`)
2. Probes which sites are logged in — for each candidate site, runs a cheap
   command and checks whether it returns `AUTH_REQUIRED` (exit 77)
3. Connects to `<CENTRAL_URL>/api/v1/nodes/ws`
4. Sends register frame with token, mode, os, probed sites, version
5. Loops: handles `collect` / `ping` frames. Each `collect` triggers
   `opencli <site> <command> …` as an asyncio subprocess; the result is
   sent back as a `result` frame. Multiple collects can run concurrently.

On disconnect: exponential backoff (min 3s → max 60s), then reconnect.

## Error handling

Exit codes from OpenCLI map to error codes in the result frame:

| exit | error code |
|------|-----------|
| 0 | — (success) |
| 1 | `GENERIC` |
| 2 | `USAGE` |
| 66 | `EMPTY` |
| 69 | `SERVICE_UNAVAILABLE` |
| 75 | `TIMEOUT` |
| 77 | `AUTH_REQUIRED` |
| 78 | `CONFIG` |

If opencli emits a structured error envelope (`{ok:false, error:{code,message}}`),
the envelope's `code` takes precedence over the exit-code mapping.

Timeouts: the agent `kill`s the subprocess when `asyncio.wait_for` expires
and reports `TIMEOUT` with exit code 75.

## Tests

```bash
.venv/bin/python -m pytest -q
# 24 passed
```

Coverage: `build_argv` edge cases (positional, flags, bools, lists,
underscore→dash), subprocess runner (happy, auth-required, error envelope
preference, timeout kill), login detection, end-to-end WS roundtrip with
an in-process fake hub.
