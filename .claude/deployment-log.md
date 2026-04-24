# Deployment log

Records of live deploys. `deployment.md` says *how* to deploy; this says
*what actually happened*. Append new entries at the top as they occur.

## 2026-04-24 — security model flip: allow-list → deny-list

### Trigger

First real Hermes dispatch worked end-to-end (reddit WSB, 10 items in 8.28s),
but on a second query Hermes hit the fleet-mcp whitelist. User message:
「為何會這樣 我不是說 opencli 所有功能都要可以用？我們的重點只是 讓人可以
統一用 hermes 來統一調度」. Original spec §5.2 intentionally used a
conservative per-site-per-command allow-list (6 sites × 3–5 commands each =
~25 pairs total). That's 25 dispatch paths vs opencli's actual surface: **101
sites, hundreds of sub-commands**. The allow-list was an order of magnitude
too small for the project's real goal.

### Decision

Flip to deny-list. Sites are a flat allow-list (anything opencli supports
except framework verbs). Sub-commands are implicitly allowed unless they
appear in `FORBIDDEN_GLOBAL` (framework) or `FORBIDDEN_PER_SITE[site]`
(writes on user account). Writes are "anything that modifies remote account
state" — post, reply, comment, like, follow, subscribe, upvote, save
(verb), bookmark (verb), publish, delete, block, etc.

### Research

- Ran `opencli --help` → 101 content sites + framework verbs + external CLI
  passthroughs (docker/gh/lark-cli/obsidian/vercel/wecom-cli).
- Spawned three general-purpose subagents in parallel, each taking ~34 sites.
  Each ran `opencli <site> --help` for its batch and classified sub-commands
  as READ / WRITE / UNSURE. Raw output committed to `.claude/tmp/
  categorization-{A,B,C}.md` as audit trail.
- Aggregate: ~447 reads, ~141 writes, 6 unsure. Unsure resolved conservatively
  (`antigravity serve` → WRITE, all AI chat `ask`/`send`/`image`/`new` →
  WRITE, `notebooklm open` → READ, `weibo post` → READ per its description).

### Changes

`fleet-mcp/src/fleet_mcp/`:
- `security.py` rewritten: `SUPPORTED_SITES` now a 101-element frozenset;
  added `FORBIDDEN_PER_SITE` dict covering 37 sites with at least one write;
  kept `FORBIDDEN_GLOBAL` for framework-level bans (`FORBIDDEN_COMMANDS` kept
  as deprecated alias for backwards compat); added `blocked_commands_for()`
  helper; rewrote `check_whitelist()` error messages. Added `SITE_DESCRIPTIONS`
  for all 101 sites.
- `schemas.py`: `SiteInfo.commands` renamed to `SiteInfo.blocked_commands`
  (semantics change — was "allowed", now "blocked writes").
- `server.py`: `list_supported_sites()` rewritten to iterate the flat set
  and inject blocked commands per site; tool docstring now documents the
  deny-list model.

`fleet-mcp/tests/`:
- `test_security.py`: whitelist tests updated for new error strings; added
  new assertions for `reddit read` (now allowed), `twitter post/like/follow`
  (still blocked), `facebook feed` (newly allowed — site was previously
  unsupported entirely).
- `test_server.py::test_list_supported_sites`: asserts ≥ 100 sites,
  `blocked_commands` shape, sample read-heavy sites have empty blocked lists.

`.claude/`:
- `spec.md` §5.2 rewritten to describe deny-list model with sample blocks.
- `deployment-log.md` (this entry).
- `tmp/categorization-{A,B,C}.md` retained as research artifact.

### Verification

- `fleet-mcp` tests: **47 passed** locally on dev mac (was 44 in allow-list
  era; net +3 for new read/write assertions).
- VPS: scp'd `security.py` / `schemas.py` / `server.py` + both test files
  into `/opt/opencli_agent/fleet-mcp/` (source tree served by the Hermes
  fleet-mcp subprocess). Verified import: `sites=101 sites_with_writes=37`.
- `check_whitelist("reddit","read")` returns `None` (unblocked).
  `check_whitelist("reddit","comment")` returns an error. Spot-checked.

### User action to activate on live Hermes

Hermes loads `mcp_servers.fleet` (the subprocess) only at startup. Exit
current `hermes` session and re-run `hermes`; fleet-mcp respawns from the
updated source and picks up the new rules.

### Not done / follow-up

- **No commit** — per project convention, user drives commits. The dev mac
  repo has the new `.claude/tmp/` files plus all source/test edits staged
  as untracked/modified.
- `deploy/vps/setup.sh` still `uv pip install -e .` (runtime only). If we
  want to run pytest on VPS we'd need `.[dev]`. Optional.

## 2026-04-24 — first live deploy (VPS + macOS agent)

### What's running

**VPS** `34.46.31.68` (GCP `us-central1-f`, instance `rudyagent`),
Ubuntu 24.04 LTS, user `rudy871211` (passwordless sudo). Repo at
`/opt/opencli_agent`, cloned from `AAAZZZR/opencli_hermes`. `fleet-hub`
on systemd; Caddy terminates TLS for `34.46.31.68.sslip.io` (Let's
Encrypt; GCP firewall 80+443 open to 0.0.0.0/0 — verified before deploy
via external `nc` returning "Connection refused" not timeout).

**Node** `home-wsl` (`id 1de60290-0f67-49ed-a4a5-45b7eb27108e`). The
label predates pivot — we planned WSL2 first, then made the dev macOS
the first agent. Label kept for now; rename when a real WSL2 node joins.

**Agent (macOS)** launchd unit `com.fleet.agent` at
`~/Library/LaunchAgents/com.fleet.agent.plist`, backed by
`~/.fleet-agent/{venv,config.env,logs}`. Hub reports `status=online`,
`os=darwin`, `opencli_version=1.7.7`.

**End-to-end smoke test** passed the pipeline (`POST /tasks` → WS →
agent → opencli subprocess). Failed at `exit_code=69 SERVICE_UNAVAILABLE`
because Chrome OpenCLI Bridge extension isn't installed yet — not a
defect. All six login probes returned `TIMEOUT`, but `login_detect`
defaults to "assume logged-in on ambiguity" so `logged_in_sites`
includes all six. Dispatch will pick this node and fail at opencli;
acceptable for now.

### Pending

1. Install Chrome OpenCLI Bridge extension on the macOS Chrome + log
   into ≥ 1 target site. Then re-run a smoke `POST /tasks` to see real
   items.
2. Merge `docs/hermes-config.yaml` into VPS `~/.hermes/config.yaml`,
   restart `hermes`, verify `list_nodes` / `dispatch_best` tools appear.
3. (Cosmetic) rename node `home-wsl` → `mac-dev` when a WSL2 node
   actually joins: `DELETE /nodes/home-wsl` + `POST /nodes {label:"mac-dev"}`
   + re-run installer.

### macOS install gotchas (for future agents)

The installer (`fleet-hub/scripts/install-agent.sh`) assumes
`npm install -g` lands its bins in `PATH`. Homebrew's node breaks that
assumption. Two distinct failures hit sequentially.

**G1 — `OPENCLI_BIN=` ends up empty in `config.env`.**
Homebrew keeps node bins in `/usr/local/Cellar/node/<ver>/bin/`
without always symlinking into `/usr/local/bin/`. The installer line
`OPENCLI_BIN=$(command -v opencli)` runs right after `npm install -g`;
without the symlink, `command -v opencli` returns empty. Agent then
execs `""` and dies with `PermissionError: [Errno 13] Permission denied: ''`
on first login probe.

Fix used here:
```sh
ln -sf /usr/local/Cellar/node/25.6.1/bin/opencli ~/.local/bin/opencli
# ~/.fleet-agent/config.env:
OPENCLI_BIN=/Users/chenjunru/.local/bin/opencli
```

**G2 — launchd's minimal PATH can't resolve opencli's shebang.**
opencli's shebang is `#!/usr/bin/env node`. launchd strips PATH; `env`
can't find `node` → `env: node: No such file or directory`, exit 127,
hub reports task `error_code=GENERIC`.

Fix: add `PATH` to plist `EnvironmentVariables`:
```xml
<key>PATH</key><string>/Users/chenjunru/.local/bin:/usr/local/Cellar/node/25.6.1/bin:/usr/local/bin:/usr/bin:/bin</string>
```
then `launchctl unload <plist> && launchctl load <plist>`.

**G3 — ~30s probe window after reload.**
`login_detect._PROBES` runs real `opencli <site> hot --limit 1` per
candidate; each 10s TIMEOUT without Bridge, six sites in parallel pairs
≈ 30s. Hub briefly reports node `offline` during that window even though
the process is up. Not a bug — wait and retry.

### Suggested installer patches (not yet applied)

In `fleet-hub/scripts/install-agent.sh`:

```sh
# robust fallback if PATH can't resolve opencli
OPENCLI_BIN=$(command -v opencli || true)
[ -z "$OPENCLI_BIN" ] && OPENCLI_BIN="$(npm prefix -g)/bin/opencli"
```

In the macOS launchd plist template generated by the same installer,
add a `PATH` env var including `$(npm prefix -g)/bin`. That makes the
installer idempotent on macOS with Homebrew node without manual fixup.

### Ops cheatsheet for this deploy

| Operation | Command |
|-----------|---------|
| Hub health (anywhere) | `curl https://34.46.31.68.sslip.io/health` |
| Hub logs (on VPS) | `sudo journalctl -u fleet-hub -f` |
| Nodes list (on VPS) | `curl localhost:8031/api/v1/nodes \| python3 -m json.tool` |
| Agent restart (macOS) | `launchctl unload ~/Library/LaunchAgents/com.fleet.agent.plist && launchctl load ~/Library/LaunchAgents/com.fleet.agent.plist` |
| Agent logs (macOS) | `tail -f ~/.fleet-agent/logs/agent.err.log` |
| SSH into VPS from dev mac | `ssh rudy871211@34.46.31.68` (ed25519 key in VM's `authorized_keys`) |

### Assets written outside the repo

**VPS `rudyagent`** (by setup.sh unless noted):
- `/opt/opencli_agent/` — repo clone
- `/etc/systemd/system/fleet-hub.service`
- `/etc/caddy/Caddyfile` (proxies `34.46.31.68.sslip.io` → `localhost:8031`)
- `/opt/opencli_agent/fleet-hub/.env` + `/opt/opencli_agent/fleet-mcp/.env`
- `~/.fleet-hub/` (audit log dir, created by setup.sh)
- `~/.ssh/authorized_keys` — *manually* added dev-mac pubkey
  `ssh-ed25519 ...HcNm1OH claude-code-macos` for remote SSH from this session

**Dev macOS**:
- `~/.fleet-agent/{venv,config.env,logs}` — installer
- `~/.fleet-agent/config.env.bak.*` — backup of broken config before fix
- `~/Library/LaunchAgents/com.fleet.agent.plist` — installer + manual PATH patch
- `~/.local/bin/opencli` → Cellar symlink (manual)
- `~/.ssh/id_ed25519{,.pub}` — generated fresh for VM SSH
