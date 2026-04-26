# opencli_agent — distributed OpenCLI fleet

Three packages that let a Hermes (or any MCP-compatible) LLM agent dispatch
`@jackwener/opencli` commands to a fleet of home laptops over reverse
WebSocket:

```
┌──────────────────────────────────────────────────────────────┐
│  VPS                                                          │
│  ┌─────────┐   stdio MCP   ┌──────────┐   localhost HTTP      │
│  │ Hermes  │ ────────────▶ │ fleet-mcp│ ──────────┐           │
│  └─────────┘               └──────────┘           ▼           │
│                                            ┌────────────┐     │
│                                            │ fleet-hub  │     │
│                                            └─────┬──────┘     │
└──────────────────────────────────────────────────┼────────────┘
                                                   │ wss:// reverse
       ┌───────────────┬───────────────┬───────────┴─────┐
       ▼               ▼               ▼                 ▼
  ┌─────────┐    ┌─────────┐     ┌─────────┐       ┌─────────┐
  │fleet-   │    │fleet-   │     │fleet-   │       │  ...    │
  │agent    │    │agent    │     │agent    │       │         │
  │opencli  │    │opencli  │     │opencli  │       │         │
  │Chrome ✓ │    │Chrome ✓ │     │Chrome ✓ │       │         │
  └─────────┘    └─────────┘     └─────────┘       └─────────┘
```

## Packages

| Path | Role | Ports |
|------|------|-------|
| [`fleet-mcp/`](./fleet-mcp) | MCP adapter; Hermes loads this over stdio | — |
| [`fleet-hub/`](./fleet-hub) | VPS central: REST + WS + pipeline + SQLite | 8031 |
| [`fleet-agent/`](./fleet-agent) | Laptop process: WS client + `opencli` subprocess runner | — |

## Why this project

- **Login state stays at home** — sensitive Chrome cookies never leave the
  laptop. The VPS only sees normalized JSON output.
- **Cloud brain, edge hands** — LLM runs in one place; browser automation
  with real logins runs at the edge.
- **NAT-friendly** — agents initiate outbound WSS to the hub; no port
  forwarding.
- **Per-node tokens** — every node authenticates with a unique token; lose
  a laptop → `DELETE /nodes/alice` and the token is dead.

## Quickstart — single machine (dev)

```bash
# 0. Prereqs: python >= 3.11, node >= 21 (for real opencli runs), uv

# 1. Install each package
for pkg in fleet-hub fleet-mcp fleet-agent; do
  (cd $pkg && uv venv .venv && uv pip install -e ".[dev]")
done

# 2. Start the hub
(cd fleet-hub && cp .env.example .env && .venv/bin/python -m fleet_hub) &

# 3. Register a node
curl -X POST http://localhost:8031/api/v1/nodes \
  -H "content-type: application/json" \
  -d '{"label":"my-laptop"}'
# Copy the token from the response.

# 4. Start the agent
(cd fleet-agent
  cp .env.example .env
  # Edit .env: set NODE_TOKEN to what /nodes returned, CENTRAL_URL=http://localhost:8031, NODE_LABEL=my-laptop
  .venv/bin/python -m fleet_agent
) &

# 5. Configure Hermes (or any MCP client) to load fleet-mcp via stdio.
#    See docs/hermes-config.yaml for a template.
```

## Run the tests

```bash
for pkg in fleet-hub fleet-mcp fleet-agent; do
  (cd $pkg && .venv/bin/python -m pytest -q)
done
# Expected: 35 + 44 + 24 = 103 passed
```

## Production layout

See [`.claude/spec.md`](./.claude/spec.md) §7 for the full VPS + per-laptop
install. The token-bearing installer endpoint is localhost-only on the VPS and
should be fetched through SSH.

## License

TBD.
