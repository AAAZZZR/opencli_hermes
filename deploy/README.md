# Deployment — VPS + laptop runbook

Target: **Nous Research Hermes** on a VPS commanding **one or more laptops**
to run `opencli` scrapes against locally-logged-in Chrome sessions.

---

## Prerequisites

**VPS:**
- Ubuntu / Debian, reachable public IP, ports 80 + 443 open to the world,
  port 22 for your SSH
- A user with sudo rights (the script assumes you sudo as your normal user —
  don't log in as root)
- Hermes Agent already installed (or install in step 5)

**Laptop (per laptop):**
- macOS / Linux / WSL2 (not Windows native — the installer uses bash)
- Node.js ≥ 21 (installer will check; install via
  [nvm](https://github.com/nvm-sh/nvm) if missing)
- Chrome with the [OpenCLI Bridge extension](https://github.com/jackwener/OpenCLI)
  loaded (extension comes with `@jackwener/opencli`'s release zip)

**GitHub repo:**
- Push this monorepo to GitHub (public preferred — private needs a PAT
  embedded in `FLEET_AGENT_INSTALL_SPEC`, which is visible to everyone
  who hits `/api/v1/nodes/install/agent.sh`).

---

## 1. VPS — one-shot install

On the VPS, as your sudo-able user:

```bash
# Clone (public repo)
sudo install -d -o $USER -g $USER /opt/opencli_agent
git clone https://github.com/<YOU>/opencli_agent.git /opt/opencli_agent
cd /opt/opencli_agent

# Run setup — <PUBLIC_IP> is your VPS's external IP
sudo ./deploy/vps/setup.sh 34.46.31.68 https://github.com/<YOU>/opencli_agent.git
```

This:
- apt-installs caddy, python3, git, curl
- Installs uv (as your user)
- Builds venvs + installs `fleet-hub` and `fleet-mcp`
- Writes `.env` files with the sslip.io hostname baked in
  (e.g. `https://34.46.31.68.sslip.io`)
- Installs + enables `fleet-hub.service` on systemd
- Configures Caddy to TLS-terminate for `<IP>.sslip.io`
- Waits for `/health` locally, prints next steps

**Firewall check:** `sudo ufw status` — if UFW is on, need `ufw allow 80` and
`ufw allow 443`. For GCP / AWS, the equivalent security group rules.

**Verify from your laptop:**

```bash
curl https://34.46.31.68.sslip.io/health
# → {"status":"ok","version":"0.1.0"}
```

(First request may take 30–60s while Caddy fetches the Let's Encrypt cert.
If it fails, `sudo journalctl -u caddy -n 100`.)

---

## 2. VPS — register a laptop

```bash
curl -X POST http://localhost:8031/api/v1/nodes \
  -H "content-type: application/json" \
  -d '{"label":"home-wsl"}'
# → {"id":"<uuid>","label":"home-wsl","token":"<token>","status":"offline",...}
```

Copy the `token` — you'll paste it on the laptop in step 3.

Or, if you want the token to stay server-side, **skip this step** — the
laptop-side installer URL includes the label, and the hub will generate the
token fresh into the install script response.

---

## 3. Laptop (WSL2 / macOS / Linux) — install the agent

On the laptop:

```bash
# Prereqs: Node.js ≥ 21
# If missing, via nvm:
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc      # or ~/.zshrc
nvm install 22

# Agent install (one-liner from the hub)
curl -fsSL "https://34.46.31.68.sslip.io/api/v1/nodes/install/agent.sh?label=home-wsl" | bash
```

This installs `@jackwener/opencli` globally, creates `~/.fleet-agent/venv`
with `fleet-agent` pip-installed from GitHub, writes
`~/.fleet-agent/config.env` (with token + central URL), installs a systemd
`--user` unit (Linux/WSL) or a launchd plist (macOS), and starts the
service.

**Check the agent's log:**

```bash
# Linux / WSL with systemd --user
journalctl --user -u fleet-agent -f

# If WSL2 systemd isn't enabled:
# edit /etc/wsl.conf on WSL to add:  [boot]\nsystemd=true
# then `wsl --shutdown` from PowerShell and reopen the shell.

# macOS
tail -f ~/.fleet-agent/logs/agent.out.log
```

You should see `registered as node home-wsl (<uuid>)`.

**Verify from VPS:**

```bash
curl http://localhost:8031/api/v1/nodes
# → [{"label":"home-wsl","status":"online",...}]
```

`status:"online"` ⇒ reverse WS tunnel confirmed.

---

## 4. VPS — test a dispatch without Hermes

```bash
curl -X POST http://localhost:8031/api/v1/tasks \
  -H "content-type: application/json" \
  -d '{"node_id":"home-wsl","site":"zhihu","command":"hot","timeout_sec":30}'
```

The laptop's agent log will show `dispatch task=... zhihu/hot`. Response
arrives with `items: [...]` if Chrome is logged in to Zhihu, or
`status:"failed"` with `error_code:"AUTH_REQUIRED"` if not (both are
"the tunnel is working" signals — the latter means log in to Zhihu on the
laptop and re-dispatch).

---

## 5. VPS — wire fleet-mcp into Hermes

If Hermes isn't installed yet:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

Then pick a model provider:

```bash
hermes model                                       # interactive picker
hermes config set OPENROUTER_API_KEY sk-or-...     # or ANTHROPIC_API_KEY, etc.
```

Merge the `mcp_servers:` snippet from `docs/hermes-config.yaml` into
`~/.hermes/config.yaml`:

```bash
# View the snippet
cat /opt/opencli_agent/docs/hermes-config.yaml

# Edit the hermes config
nano ~/.hermes/config.yaml
# Ensure the mcp_servers.fleet block is present, with these exact paths:
#   command: "/opt/opencli_agent/fleet-mcp/.venv/bin/python"
#   args: ["-m", "fleet_mcp"]
#   env:
#     HUB_URL: "http://localhost:8031"
#     PYTHONPATH: "/opt/opencli_agent/fleet-mcp/src"
```

**Important:** Hermes does NOT honour a `cwd:` field (confirmed against
upstream `tools/mcp_tool.py`). Use the absolute path to the venv python
plus `PYTHONPATH` in `env:` as shown.

Start Hermes:

```bash
hermes           # start chatting
```

Ask it in natural language: *"List my fleet nodes"* → Hermes should call
`fleet.list_nodes` and return the laptop status. *"Fetch the Zhihu hot list
from home-wsl"* → Hermes should call `fleet.dispatch` (or
`fleet.dispatch_best`) and summarize the result.

Useful pre-flight commands that appear in the Hermes docs:

```bash
hermes model                                  # interactive model picker
hermes config set OPENROUTER_API_KEY sk-or-…  # or ANTHROPIC_API_KEY / OPENAI_API_KEY
hermes setup                                  # full config wizard
```

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Caddy can't get TLS cert | Ports 80 + 443 open to 0.0.0.0; `journalctl -u caddy -f` |
| Laptop agent can't connect | `curl https://<ip>.sslip.io/health` from laptop; token correct |
| `AUTH_REQUIRED` in dispatch | Log in to the site in the laptop's Chrome; restart `fleet-agent` to re-probe |
| `CONFIG` exit code | `opencli --version` on laptop — binary missing or wrong PATH |
| `OPENCLI_BIN` wrong path | Edit `~/.fleet-agent/config.env` and `systemctl --user restart fleet-agent` |
| Hermes doesn't see fleet tools | `hermes` logs — MCP server connect errors usually print at startup |

Audit logs:

- Hub: `~/.fleet-hub/audit.log` (node connects, task lifecycle)
- fleet-mcp: `~/.fleet-mcp/audit.log` (tool calls, rate-limit decisions)

---

## Updating

```bash
# On VPS
cd /opt/opencli_agent
git pull
(cd fleet-hub && .venv/bin/python -m pip install -e .)
(cd fleet-mcp && .venv/bin/python -m pip install -e .)
sudo systemctl restart fleet-hub

# On laptop — re-run the installer (idempotent)
curl -fsSL "https://<ip>.sslip.io/api/v1/nodes/install/agent.sh?label=<label>" | bash
```
