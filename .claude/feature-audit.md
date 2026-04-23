# opencli-admin Feature Audit & Rewrite Decision (2026-04-23)

> History note: the user evaluated `xjh1994/opencli-admin` as a possible
> upstream dependency, then decided on 2026-04-23 to rewrite the relevant
> pieces ourselves as `fleet-hub` + `fleet-agent`. This doc records what
> was dropped vs what we re-implemented, so future work doesn't have to
> re-derive the rationale.

## Source

- **opencli-admin repo:** https://github.com/xjh1994/opencli-admin
- **opencli-admin size:** ~24,700 LOC (8K Python backend + 10K TS frontend + 6.5K tests)
- **Our implementation:** `fleet-hub/` + `fleet-agent/` in this monorepo

---

## What we re-implemented from scratch

All implemented in `fleet-hub/src/fleet_hub/` or `fleet-agent/src/fleet_agent/`:

| Feature | Where | Notes |
|---------|-------|-------|
| WS hub (connection manager + dispatch futures) | `fleet-hub/ws/manager.py` | asyncio.Future per task, pending cleanup on disconnect |
| Nodes CRUD + WS endpoint | `fleet-hub/api/nodes.py` | Accepts label or UUID, token on WS handshake |
| Tasks REST (POST / GET / records) | `fleet-hub/api/tasks.py` | `wait=true` for synchronous dispatch |
| Collection pipeline | `fleet-hub/pipeline/{normalize,store}.py` | Field-alias → content-hash → insert-if-new |
| Content dedup (SHA-256) | `fleet-hub/pipeline/normalize.py` | Keyed on `site\|command\|id\|title\|url\|content`, per-task unique |
| Install script | `fleet-hub/scripts/install-agent.sh` + `api/install.py` | Served by `/api/v1/nodes/install/agent.sh?label=X`, with token substituted |
| Agent WS client | `fleet-agent/ws_client.py` | Reconnect, ping/pong, concurrent dispatch handling |
| Agent subprocess runner | `fleet-agent/runner.py` | Maps OpenCLI exit codes (77=AUTH_REQUIRED etc), timeout-kill, error-envelope parse |
| Login detection | `fleet-agent/login_detect.py` | Cheap probes per candidate site at register time |

Per-node tokens are first-class, unlike opencli-admin where they don't exist:
a `token` column on `nodes`, generated on `POST /nodes`, validated on WS
handshake.

---

## What we intentionally dropped (and why)

| Feature (opencli-admin LOC) | Why dropped |
|--------|-------|
| Celery + beat + Redis (workers API) | 2–10 nodes doesn't need distributed task queue; asyncio is enough. |
| AI Agent CRUD (23 LOC) + providers CRUD (20) | Hermes IS the AI layer — we don't need hub-side LLM calls. |
| 3 AI processors (Claude/OpenAI/Local, ~237 LOC) | Same. |
| Browser pool (local+Redis, ~319 LOC) | Chrome lives on laptops, not the VPS. No shared pool needed. |
| Docker Chrome management (~150 LOC) | Laptops manage their own Chrome (manual login). |
| Chrome extension "Bridge mode" admin (~700 LOC) | Bridge is opencli's own; admin doesn't need to configure it. |
| `cli` channel (arbitrary subprocess) | Security — we explicitly forbid `register`/`install`/`plugin` in fleet-mcp. |
| `rss` / `api` / `web_scraper` channels (~253 LOC) | Phase 3 maybe. For now, all collection is via `opencli` (agent-executed). |
| Notification rules engine + Webhook/Email/DingTalk/Feishu/WeCom (~361 LOC) | Hermes + Telegram/email already cover this. |
| System API (live `.env` edit, restart) | Edit config and restart the service manually. |
| React frontend (~10K LOC) + i18n (~1K) + Dashboard stats (223) | CLI-first via Hermes. The `docs/` has curl examples. |

---

## Implementation stats

From the current repo:

| Package | Source LOC | Test LOC | Tests |
|---------|-----------|----------|-------|
| `fleet-mcp` | ~600 | ~500 | 44 passing |
| `fleet-hub` | ~700 | ~600 | 35 passing |
| `fleet-agent` | ~400 | ~400 | 24 passing |
| **Total** | **~1,700** | **~1,500** | **103** |

Compared to opencli-admin's 8,000-LOC backend, we ship ~20% of the code for
the pieces we need. The rest is covered by Hermes.

---

## What the rewrite also fixed (vs reuse)

1. **Endpoint path:** opencli-admin uses `/api/v1/sources`, we use
   `/api/v1/tasks` directly (no Source abstraction — Hermes requests a
   site+command, hub dispatches).
2. **Per-node tokens:** first-class. opencli-admin has no per-node auth.
3. **WS auth on handshake:** opencli-admin accepts any `agent_url` without
   verification. We validate a token.
4. **Node identifier:** accepts either label or UUID in REST calls; Hermes
   can use the human-friendly label.
5. **Async-friendly contract:** `POST /tasks` with `wait=true` does the
   whole thing in one round-trip (dispatch → wait for WS result → store).
   opencli-admin requires caller to poll `GET /tasks/{id}`.
6. **Error-code taxonomy:** OpenCLI exit codes (66/69/75/77/78) propagate
   through WS result frames to hub's `Task.error_code` to fleet-mcp's
   `DispatchResult.error_code` — useful for Hermes to distinguish "logged
   out" from "timeout" from "empty results".
