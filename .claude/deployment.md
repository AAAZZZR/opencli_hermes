# Deployment runbook — this project's real values baked in

Project-specific version of `deploy/README.md`. Uses the actual repo URL,
VPS IP, and sslip.io hostname for this project. If you're forking for
somebody else's setup, use `deploy/README.md` as the template and
substitute.

- **Repo:** `https://github.com/AAAZZZR/opencli_hermes`
- **VPS public IP:** `34.46.31.68`
- **Public hostname:** `34.46.31.68.sslip.io` (sslip.io resolves it back to the IP, no DNS needed)
- **Agent platform:** WSL2 on the Windows laptop

---

## Prerequisites check

| Item | Status |
|------|--------|
| GCP VPS running Ubuntu/Debian with external IP `34.46.31.68` | have |
| Hermes Agent installed on the VPS | have |
| WSL2 on the Windows laptop | have |
| Ports 80 + 443 open in GCP firewall to 0.0.0.0/0 | **verify before running setup** |
| Repo pushed to GitHub as `AAAZZZR/opencli_hermes` | done |
| Repo is **public** (or you have a PAT strategy) | **verify — private breaks agent install** |

GCP firewall quick check from the VPS:

```bash
# Outbound — check you can reach Let's Encrypt
curl -I https://acme-v02.api.letsencrypt.org/directory

# Inbound — from your laptop, try to reach port 80 before Caddy is up.
# (If a request hangs rather than "connection refused", firewall blocks it.)
```

If 80/443 aren't open, in GCP Console → VPC network → Firewall → create:

- `default-allow-http` (tcp:80, source 0.0.0.0/0)
- `default-allow-https` (tcp:443, source 0.0.0.0/0)

---

## 1. VPS — one-shot install

SSH to the VPS as your sudo-capable user (not root):

```bash
# Clone the repo
sudo install -d -o $USER -g $USER /opt/opencli_agent
git clone https://github.com/AAAZZZR/opencli_hermes.git /opt/opencli_agent
cd /opt/opencli_agent

# Run the setup script
sudo ./deploy/vps/setup.sh 34.46.31.68 https://github.com/AAAZZZR/opencli_hermes.git
```

The setup script:
- apt-installs Caddy, python3, git, curl
- Installs `uv` (as your user)
- Builds venvs for `fleet-hub` and `fleet-mcp`, `pip install -e` into each
- Writes `fleet-hub/.env` with `PUBLIC_URL=https://34.46.31.68.sslip.io` and
  `FLEET_AGENT_INSTALL_SPEC=git+https://github.com/AAAZZZR/opencli_hermes.git#subdirectory=fleet-agent`
- Writes `fleet-mcp/.env` with `HUB_URL=http://localhost:8031`
- Installs + enables `fleet-hub.service` on systemd
- Writes `/etc/caddy/Caddyfile` for `34.46.31.68.sslip.io` → `localhost:8031`
- Reloads Caddy
- Waits for `/health` on localhost

Expected output ends with:

```
==> fleet-hub OK on localhost
Public URL:   https://34.46.31.68.sslip.io
Test:         curl https://34.46.31.68.sslip.io/health
```

### Verify (from your laptop, not the VPS)

```bash
curl https://34.46.31.68.sslip.io/health
# → {"status":"ok","version":"0.1.0"}
```

First request can take 30–60s while Caddy does the ACME dance. If it
fails after 2 minutes: `ssh` in and `sudo journalctl -u caddy -n 100` — most
likely port 80 isn't reachable from the public internet.

---

## 2. VPS — register the WSL node

```bash
curl -X POST http://localhost:8031/api/v1/nodes \
  -H "content-type: application/json" \
  -d '{"label":"home-wsl"}'
```

Response includes a `token` field — **do not lose this**. It's only
returned at creation. If lost, `DELETE /api/v1/nodes/home-wsl` and
recreate.

---

## 3. Laptop — install fleet-agent

> **Note (post-2026-04-26):** the public Caddy reverse proxy now allows
> only `/health` and `/api/v1/nodes/ws`. Everything else under `/api/v1/*`
> returns 403 — installer endpoint, REST CRUD, dispatch, all of it. The
> 2026-04-24 entry in `deployment-log.md` first blocked the installer
> (token-leak fix); the 2026-04-26 entry widened the block to the rest of
> REST after audit found it publicly reachable without auth.
>
> All admin REST calls and installer fetches now SSH to the VPS and hit
> `localhost:8031`. The agent's reverse-WS connection to
> `/api/v1/nodes/ws` is unaffected — that endpoint stays public and
> authenticates via the per-node token in the register frame.
>
> Future: see `.claude/develop/install-ticket.md` for a planned one-time
> URL that would let us re-open the installer route safely. Not built yet.

In a terminal on the laptop:

```bash
# Prereqs — Node.js >= 21 via nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 22

# Agent install via SSH pipe. The laptop fetches the installer from
# localhost:8031 on the VPS (reached through SSH) and runs it.
ssh rudy871211@34.46.31.68 \
    'curl -s "http://localhost:8031/api/v1/nodes/install/agent.sh?label=home-wsl"' \
  | bash
```

One-line shell function for frequent use — drop in `~/.zshrc` / `~/.bashrc`:

```bash
fleet-install() {
    local label="${1:?usage: fleet-install <label>}"
    ssh rudy871211@34.46.31.68 \
        "curl -s 'http://localhost:8031/api/v1/nodes/install/agent.sh?label=$label'" | bash
}
# then:  fleet-install home-wsl
```

The installer substitutes the NODE_TOKEN into the script at the hub side
(via `shlex.quote` — label is regex-validated upstream), so the token
arrives inside the SSH-encrypted stream and never touches shell history.

### WSL2 + systemd gotcha

The installer prefers `systemd --user`. WSL2 does NOT enable systemd by
default. Check:

```bash
systemctl --user status
# If you see "Failed to connect to bus", systemd is off.
```

Enable: edit `/etc/wsl.conf` (create if missing):

```ini
[boot]
systemd=true
```

Then from PowerShell (not WSL): `wsl --shutdown`. Reopen a WSL shell.
Re-run the installer — it'll now install a `systemd --user` unit.

If you refuse to enable systemd, the installer falls back to `nohup`.

### Verify

```bash
# WSL logs
journalctl --user -u fleet-agent -f
# Expected line: "registered as node home-wsl (<uuid>)"
```

On the VPS:

```bash
curl http://localhost:8031/api/v1/nodes
# → [{"label":"home-wsl","status":"online",...,"logged_in_sites":[...]}]
```

`status:"online"` ⇒ reverse WSS tunnel from laptop to VPS is confirmed.

---

## 4. Smoke test without Hermes

Tests that the VPS can actually drive a command on the laptop. From the VPS:

```bash
curl -X POST http://localhost:8031/api/v1/tasks \
  -H "content-type: application/json" \
  -d '{"node_id":"home-wsl","site":"zhihu","command":"hot","timeout_sec":30}'
```

Watch the laptop's agent log — should show `dispatch task=... zhihu/hot`.
The response comes back to the VPS:

- `status:"completed"` + `items: [...]` ⇒ Chrome was logged in and opencli
  scraped successfully
- `status:"failed"` + `error_code:"AUTH_REQUIRED"` ⇒ tunnel works, but log
  in to Zhihu on the laptop's Chrome and retry
- `status:"failed"` + `error_code:"CONFIG"` ⇒ `opencli` binary missing on
  the laptop (check `opencli --version`)

---

## 5. Wire fleet-mcp into Hermes on the VPS

Hermes is already installed per user.

### 5.1 Set an LLM provider (one-time)

```bash
hermes model                                  # interactive picker
# or
hermes config set OPENROUTER_API_KEY sk-or-...
# or ANTHROPIC_API_KEY / OPENAI_API_KEY
```

### 5.2 Add fleet-mcp to config

Open `~/.hermes/config.yaml` and merge in the snippet from
`/opt/opencli_agent/docs/hermes-config.yaml`. Target shape:

```yaml
mcp_servers:
  fleet:
    command: "/opt/opencli_agent/fleet-mcp/.venv/bin/python"
    args: ["-m", "fleet_mcp"]
    env:
      HUB_URL: "http://localhost:8031"
      PYTHONPATH: "/opt/opencli_agent/fleet-mcp/src"
      MAX_ITEMS_INLINE: "50"
      TASK_TIMEOUT_SEC: "120"
      LOG_LEVEL: "INFO"
    connect_timeout: 30
    timeout: 180
    tools:
      include:
        - list_nodes
        - list_supported_sites
        - dispatch
        - dispatch_best
        - broadcast
        - get_task_status
```

**Gotchas (verified against Hermes' `tools/mcp_tool.py` source):**

- No `cwd:` field — use absolute python path + `PYTHONPATH` in `env:`.
- Hermes filters subprocess env: only a safe baseline + keys listed under
  `env:` get passed through. If fleet-mcp needs something not listed, add
  it here.

### 5.3 Start Hermes

```bash
hermes
```

Then talk to it in natural language. A few tries:

- *"List my fleet nodes"* — should call `fleet.list_nodes`
- *"Fetch the zhihu hot list"* — should call `fleet.dispatch_best` with
  `site=zhihu, command=hot`
- *"Summarize what's trending on zhihu today"* — multi-step: dispatch + LLM
  summarization of the returned items

---

## Operations

| Operation | Command |
|-----------|---------|
| Hub logs (VPS) | `sudo journalctl -u fleet-hub -f` |
| Caddy logs (VPS) | `sudo journalctl -u caddy -f` |
| Agent logs (laptop) | `journalctl --user -u fleet-agent -f` |
| Hub audit log (VPS) | `tail -f ~/.fleet-hub/audit.log` |
| MCP audit log (VPS) | `tail -f ~/.fleet-mcp/audit.log` |
| Restart hub | `sudo systemctl restart fleet-hub` |
| Restart agent (WSL) | `systemctl --user restart fleet-agent` |
| Re-register a node | `DELETE /api/v1/nodes/<label>` then `POST /api/v1/nodes` |
| Pull latest (VPS) | `cd /opt/opencli_agent && git pull && sudo systemctl restart fleet-hub` |
| Pull latest (laptop) | re-run the installer |

---

## Troubleshooting quickref

| Symptom | First check |
|---------|-------------|
| `curl https://.../health` hangs | GCP firewall port 443 |
| `curl https://.../health` SSL error | Caddy couldn't get LE cert — port 80 firewall |
| Node stays `offline` | Agent log; is `CENTRAL_URL` reachable from laptop? |
| Dispatch returns `NODE_OFFLINE` | Agent disconnected; check `journalctl --user -u fleet-agent` |
| Dispatch returns `AUTH_REQUIRED` | Log in to the site in laptop's Chrome |
| Dispatch returns `CONFIG` | `opencli --version` on laptop; fix PATH in `~/.fleet-agent/config.env` |
| Dispatch returns `TIMEOUT` | Bump `timeout_sec` in the POST body; check network |
| Hermes says "no tool named fleet.list_nodes" | `~/.hermes/config.yaml` malformed; run `hermes` again, watch early log lines |
