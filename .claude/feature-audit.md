# opencli-admin Feature Audit & Rewrite Decision Matrix

> Comprehensive comparison of opencli-admin features against our spec requirements.
> Used to decide what to keep, simplify, or drop in the rewrite.

## Source

- **opencli-admin repo:** https://github.com/xjh1994/opencli-admin
- **Total LOC:** ~24,700 (8K Python backend + 10K TypeScript frontend + 6.5K tests)
- **Our spec:** `.claude/spec.md`

---

## Core Infrastructure

| Feature | opencli-admin | Lines | Decision | Rationale |
|---------|--------------|-------|----------|-----------|
| WS Hub (ws_agent_manager) | asyncio.Future dispatch, ping/pong keepalive | 107 | ✅ Keep | Core of the system — nodes connect via WSS |
| Node Management | Register, online/offline tracking, events, stats | 533 | ✅ Simplify | Keep register + status. Drop per-node stats/events detail |
| DB + Migrations | SQLAlchemy async + Alembic, SQLite/Postgres | 97 | ✅ Simplify | SQLite only for Phase 1. Keep Alembic |
| Config System | pydantic-settings, env-driven | 106 | ✅ Keep | Already have this |
| Health Endpoint | GET /health | trivial | ✅ Keep | Monitoring baseline |

## Collection Pipeline

| Feature | opencli-admin | Lines | Decision | Rationale |
|---------|--------------|-------|----------|-----------|
| Pipeline Orchestrator | collect→normalize→store→AI→notify | 252 | ⚡ Simplify | Keep collect→normalize→store. AI and notify as optional hooks |
| opencli Channel | Local/HTTP/WS dispatch, parser, pool integration | 469 | ⚡ Simplify | Drop local execution path. Keep WS agent dispatch only |
| web_scraper Channel | httpx + BeautifulSoup | 72 | 🤔 Optional | Direct web scraping without opencli |
| api Channel | REST/GraphQL with auth | 111 | 🤔 Optional | Direct API calls, useful for structured data sources |
| rss Channel | feedparser | 70 | 🤔 Optional | Spec Phase 3 mentions "non-opencli channels: RSS, direct API" |
| cli Channel | Arbitrary subprocess | 87 | ❌ Drop | Security risk — equivalent to eval/exec |
| Normalizer | Field aliasing to standard schema | 96 | ✅ Keep | Unifies output across different sites |
| Dedup (content hash) | SHA-256 per source | 52 | ✅ Keep | Prevents duplicate records |
| Pipeline Events | Structured execution trace per run | 32 | ⚡ Simplify | Keep basic logging. Drop TaskRunEvent table |

## Scheduling

| Feature | opencli-admin | Lines | Decision | Rationale |
|---------|--------------|-------|----------|-----------|
| Cron Scheduler | croniter, 60s poll loop | 82 | ✅ Keep | Required — "every morning, check zhihu hot list" |
| One-time Schedules | is_one_time flag, auto-disable | trivial | ✅ Keep | Comes with cron for free |
| Timezone Support | Field exists but UTC evaluation | trivial | ⚡ Fix | Store and actually evaluate in correct timezone |
| Celery Beat | Distributed scheduling | 82 | ❌ Drop | Using local executor, no Celery |

## AI Processing

| Feature | opencli-admin | Lines | Decision | Rationale |
|---------|--------------|-------|----------|-----------|
| Claude Processor | Per-record AI enrichment | 80 | ❌ Drop | Hermes IS the LLM — it summarizes results directly |
| OpenAI Processor | Per-record AI enrichment | 84 | ❌ Drop | Same reason |
| Local Processor | Ollama/OpenAI-compat | 73 | ❌ Drop | Same reason |
| Model Provider CRUD | Store API keys, models | 20 | ❌ Drop | Not needed without AI processors |
| AI Agent CRUD | Prompt templates, configs | 23 | ❌ Drop | Not needed without AI processors |

## Notifications

| Feature | opencli-admin | Lines | Decision | Rationale |
|---------|--------------|-------|----------|-----------|
| Notification Rules Engine | Source-triggered rules | 100 | 🤔 Optional | Hermes could handle this, but built-in is nice |
| Webhook Notifier | HTTP POST + HMAC | 42 | 🤔 Optional | Generic integration point |
| Email Notifier | SMTP + aiosmtplib | 50 | 🤔 Optional | Direct email alerts |
| DingTalk Notifier | 钉钉 robot webhook | 63 | 🤔 Optional | Chinese team communication |
| Feishu Notifier | 飞书 incoming webhook | 70 | 🤔 Optional | Chinese team communication |
| WeCom Notifier | 企业微信 group robot | 36 | 🤔 Optional | Chinese team communication |

## Browser Management

| Feature | opencli-admin | Lines | Decision | Rationale |
|---------|--------------|-------|----------|-----------|
| Browser Pool (Local) | Per-endpoint asyncio.Queue slots | 319 | ❌ Drop | Chrome runs on laptops, not VPS |
| Browser Pool (Redis) | Distributed locking | (above) | ❌ Drop | No Redis, no distributed pool |
| Browser Bindings | site→Chrome endpoint | 14 | ⚡ Merge | Replace with node_sites.yaml mapping |
| Docker Chrome Mgmt | Dynamic container create/delete | ~150 | ❌ Drop | Laptops manage their own Chrome |
| Chrome Extension | Bridge mode extension | ~700 | ❌ Drop | CDP mode only |

## Node Agent Side

| Feature | opencli-admin | Lines | Decision | Rationale |
|---------|--------------|-------|----------|-----------|
| agent_server.py | FastAPI + WS client + opencli runner | 448 | ⚡ Simplify | Drop HTTP mode, Docker remapping. Keep WS + opencli exec |
| Install Script | Docker + Python dual-mode installer | 303 | ⚡ Simplify | Drop Docker install path. Native Python only |
| WS Auto-reconnect | Exponential backoff 3s–60s | ~30 | ✅ Keep | Laptops disconnect frequently |
| Ping/Pong Keepalive | 30s interval, 10s timeout | ~10 | ✅ Keep | Detect dead connections |

## Storage & Query

| Feature | opencli-admin | Lines | Decision | Rationale |
|---------|--------------|-------|----------|-----------|
| Records CRUD | Store, search, batch delete | 80 | ✅ Keep | Collected data must persist |
| Tasks CRUD | Task lifecycle tracking | 120 | ✅ Simplify | Keep task records. Drop runs/events detail |
| Sources CRUD | Data source definitions | 81 | ✅ Keep | Defines "what to collect" |
| Webhook Trigger | External HMAC-verified trigger | 65 | 🤔 Optional | Let external systems trigger collection |

## Frontend & Monitoring

| Feature | opencli-admin | Lines | Decision | Rationale |
|---------|--------------|-------|----------|-----------|
| React Frontend | 10 pages, full CRUD UI | ~10K | 🤔 Phase 2 | CLI-first via Hermes. UI later |
| Dashboard Stats | Aggregated queries, charts | 223 | 🤔 Phase 2 | Nice but not MVP |
| i18n (EN/ZH) | Full bilingual support | ~1K | 🤔 Phase 2 | Comes with frontend |

## Definitively Dropped

| Feature | Rationale |
|---------|-----------|
| Celery (worker/beat/redis) | 2–10 nodes doesn't need distributed task queue |
| AI Agent + Provider management | Hermes is the AI layer |
| Browser Pool (local + Redis) | Chrome is on laptops, not VPS |
| Chrome Extension (Bridge mode) | CDP mode only |
| cli Channel | Security risk — arbitrary command execution |
| System API (live .env edit) | Edit config and restart |
| Workers API (Celery inspect) | No Celery |
| Docker Chrome management | Laptops manage their own Chrome |

---

## Estimated Rewrite Size

| Component | Est. Lines | Based On |
|-----------|-----------|----------|
| Hub (FastAPI + WS + HTTP API) | ~400 | opencli-admin ws_manager(107) + nodes(533) simplified |
| MCP Server (6 tools) | ~250 | Already written |
| Security (whitelist, rate limit, audit, sanitize) | ~150 | Already written |
| Pipeline (collect→normalize→store) | ~300 | opencli-admin pipeline(252) + normalizer(96) + storer(52) simplified |
| Scheduler (cron) | ~80 | Reuse opencli-admin's approach |
| Agent (laptop-side) | ~200 | opencli-admin agent_server(448) simplified |
| Config + Schemas + DB models | ~200 | Already partially written |
| **Total** | **~1,600** | vs. opencli-admin's ~8,000 backend |
