# Deployment: VPS + Laptop Runbook

Target: a VPS running Hermes plus `fleet-mcp` and `fleet-hub`, with one or
more laptops connecting over reverse WebSocket to run OpenCLI against local
Chrome sessions.

## Prerequisites

VPS:
- Ubuntu or Debian with public ports 80 and 443 open, plus SSH.
- A normal sudo-capable user. The setup script expects to be run via `sudo`.
- Hermes Agent installed, or install it after the hub is up.

Laptop:
- macOS, Linux, or WSL2. Native Windows is not supported by the bash installer.
- Node.js >= 21.
- Chrome with the OpenCLI Bridge extension and any target sites logged in.

Repository:
- Push this monorepo to GitHub. A public repo is simplest.
- For private repos, plan `FLEET_AGENT_INSTALL_SPEC` carefully. It is embedded
  into the rendered installer script alongside the node token, so installer
  fetches must stay on the VPS localhost path.

## 1. VPS: Install Central Services

```bash
sudo install -d -o $USER -g $USER /opt/opencli_agent
git clone https://github.com/<YOU>/opencli_agent.git /opt/opencli_agent
cd /opt/opencli_agent

sudo ./deploy/vps/setup.sh <PUBLIC_IP> https://github.com/<YOU>/opencli_agent.git
```

The setup script installs OS packages, Caddy, `uv`, editable venvs for
`fleet-hub` and `fleet-mcp`, a systemd unit for `fleet-hub`, and a Caddyfile
for `<PUBLIC_IP>.sslip.io`.

Public Caddy exposure is intentionally narrow:
- `GET /health` is public.
- `WS /api/v1/nodes/ws` is public for agents and uses per-node token auth.
- All other `/api/v1/*` REST routes are localhost-only.

Verify from another machine:

```bash
curl https://<PUBLIC_IP>.sslip.io/health
```

## 2. VPS: Register A Node

Run this on the VPS:

```bash
curl -X POST http://localhost:8031/api/v1/nodes \
  -H "content-type: application/json" \
  -d '{"label":"home-wsl"}'
```

The response includes a token. The installer endpoint also embeds that token
into the generated script, so do not fetch the installer over the public
reverse proxy.

## 3. Laptop: Install Agent

Run this on the laptop. SSH reaches the VPS-local installer endpoint and pipes
the rendered script into bash:

```bash
ssh <vps-user>@<PUBLIC_IP> \
  'curl -s "http://localhost:8031/api/v1/nodes/install/agent.sh?label=home-wsl"' \
  | bash
```

The installer installs OpenCLI globally, creates `~/.fleet-agent/venv`, installs
`fleet-agent`, writes `~/.fleet-agent/config.env`, and installs launchd
on macOS or `systemd --user` on Linux/WSL, falling back to `nohup` when user
systemd is unavailable.

Check logs:

```bash
journalctl --user -u fleet-agent -f
tail -f ~/.fleet-agent/logs/agent.out.log
```

Verify from the VPS:

```bash
curl http://localhost:8031/api/v1/nodes
```

## 4. VPS: Smoke Test Dispatch

```bash
curl -X POST http://localhost:8031/api/v1/tasks \
  -H "content-type: application/json" \
  -d '{"node_id":"home-wsl","site":"zhihu","command":"hot","timeout_sec":30}'
```

`completed` means the tunnel and OpenCLI path worked. `AUTH_REQUIRED` means the
tunnel worked but Chrome needs a site login. `CONFIG` usually means OpenCLI or
Node is not on the agent service PATH.

## 5. VPS: Wire Fleet MCP Into Hermes

Merge `docs/hermes-config.yaml` into `~/.hermes/config.yaml`. The important
shape is:

```yaml
mcp_servers:
  fleet:
    command: "/opt/opencli_agent/fleet-mcp/.venv/bin/python"
    args: ["-m", "fleet_mcp"]
    env:
      HUB_URL: "http://localhost:8031"
      PYTHONPATH: "/opt/opencli_agent/fleet-mcp/src"
```

Hermes does not honor `cwd:` for stdio MCP servers; use the absolute venv
Python path and pass `PYTHONPATH` explicitly.

## Updating

On the VPS:

```bash
cd /opt/opencli_agent
git pull
(cd fleet-hub && .venv/bin/python -m pip install -e .)
(cd fleet-mcp && .venv/bin/python -m pip install -e .)
sudo systemctl restart fleet-hub
sudo systemctl reload caddy
```

On each laptop, re-run the SSH installer command from step 3.
