# Install ticket: one-time URL so new laptops don't need VPS SSH

**Status:** designed, not coded.
**Effort:** ~80 LOC in `fleet-hub`, 1 new DB table, ~5 new tests.
**Triggers for building:** anyone-but-you wants to install an agent; OR
you hit the SSH-pipe flow often enough that it's annoying; OR you want a
copy-pasteable URL you can drop into a ticket/chat instead of a pipe.

## Background

After 2026-04-24's security hardening (commit `34536f2`), the installer
endpoint `/api/v1/nodes/install/agent.sh` is blocked from the public Caddy
reverse proxy. The current new-node flow is:

```bash
# On the laptop:
ssh rudy871211@34.46.31.68 \
    'curl -s "http://localhost:8031/api/v1/nodes/install/agent.sh?label=mac-dev"' \
  | bash
```

This works for a solo admin (you always have SSH to your own VPS). It
doesn't work when you want to hand someone else an install URL, or when
you're on a device without the SSH key.

## What we want

```bash
# On VPS
curl -sX POST localhost:8031/api/v1/nodes -d '{"label":"mac-dev"}' | jq
# →
#   {
#     "label": "mac-dev",
#     "token": "<NODE_TOKEN, permanent>",
#     "install_url": "https://34.46.31.68.sslip.io/api/v1/nodes/install/agent.sh?ticket=<TICKET>",
#   }

# Copy `install_url` → give to laptop by any means (IM, email, screen-share)

# On laptop (no SSH needed):
curl -fsSL "<install_url>" | bash
# Runs once, installs agent, writes NODE_TOKEN into config.env,
# starts service. Ticket is now consumed; the URL is dead.
# Agent uses NODE_TOKEN forever — the ticket was only for pickup.
```

## Design

### Ticket semantics: single-use, no expiry

Two axes:
- **Single-use** vs reusable: **single-use**. After one successful fetch,
  ticket is marked consumed and further requests return 410 Gone. Prevents
  "install URL leaked in Slack" from being replayed.
- **Time-limited** vs indefinite: **indefinite**. Admin issues ticket,
  user might not install until tomorrow. No "oh it expired" UX.

If future-us wants to add a TTL for defence-in-depth, it's one `expires_at`
column and one `if now > expires_at` check — revisit then.

### DB schema

New table in `fleet_hub.models`:

```python
class InstallTicket(Base):
    __tablename__ = "install_tickets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # secrets.token_urlsafe(32)
    label: Mapped[str] = mapped_column(String(64), ForeignKey("nodes.label", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

ON DELETE CASCADE: deleting a node invalidates all its tickets.

### Endpoint changes

**`POST /api/v1/nodes`** (nodes.py)
- After creating the Node, also create an `InstallTicket(label=node.label)`.
- Add `install_url` to the response, built from `settings.public_url`.

**`GET /api/v1/nodes/install/agent.sh`** (install.py)
- Query param changes from `label` → `ticket`.
- Look up ticket. 404 if missing. 410 if `consumed_at is not None`.
- Mark ticket consumed atomically (SELECT ... FOR UPDATE or a conditional
  UPDATE — race between two concurrent redemptions should result in one
  success + one 410).
- Render installer using ticket's `label` → find node → use that node's
  token. Same render path as today, just a different lookup key.

**New: `POST /api/v1/nodes/{label}/tickets`** (nodes.py)
- For an existing node, generate a fresh ticket (without creating a new
  node). Returns `install_url`. Useful if original ticket was consumed by
  a failed install or lost.

### Caddyfile

Remove the current 403 block on `/api/v1/nodes/install/*`. Ticket auth
now protects the endpoint; localhost-only is no longer needed.

Keep the block as **defence-in-depth**? Probably not — it'd negate the
whole point. Just rely on ticket auth + the `@installer` matcher can be
deleted entirely.

### Rate limiting

Single endpoint, single-use per ticket, so brute-force doesn't help
(attacker would need to guess a `secrets.token_urlsafe(32)` — 256 bits of
entropy). Don't add rate limiting unless we see abuse.

## Implementation checklist

- [ ] `models.py`: add `InstallTicket` + relationship on `Node`.
- [ ] DB migration: `create_all` handles new table since spec §10 notes
      "create_all at startup" is still the status quo. Alembic migration
      only if/when Alembic ships.
- [ ] `schemas.py`: `NodeCreated` gets `install_url: str` field.
- [ ] `api/nodes.py`:
  - `POST /nodes` issues a ticket in the same transaction as node
    creation, builds `install_url = f"{settings.public_url}/api/v1/nodes/install/agent.sh?ticket={ticket.id}"`.
  - New `POST /nodes/{label}/tickets` endpoint.
- [ ] `api/install.py`:
  - Rename query param `label` → `ticket`.
  - Look up ticket + node + consume.
  - Keep `shlex.quote` + `_render` logic unchanged.
- [ ] `deploy/vps/Caddyfile`: delete the `@installer` 403 block.
- [ ] `scripts/install-agent.sh`: unchanged (template still substitutes
      token into the rendered script).
- [ ] Tests (`tests/test_install.py` + `tests/test_nodes.py`):
  - POST /nodes returns install_url; URL contains a valid ticket.
  - GET /install/agent.sh?ticket=valid → 200 with script containing
    token + marks ticket consumed.
  - GET /install/agent.sh?ticket=consumed → 410.
  - GET /install/agent.sh?ticket=nonexistent → 404.
  - GET /install/agent.sh without `ticket` param → 422.
  - POST /nodes/{label}/tickets creates a new ticket for existing node.
  - DELETE /nodes/{label} CASCADEs tickets.
  - Concurrent redemption: one succeeds, one 410.

## Docs to update on merge

- `.claude/spec.md` §4.1 (REST API): rename query param in the `GET /install/...` line.
- `.claude/spec.md` §5: add a short "Install tickets" subsection.
- `.claude/deployment.md` §3: new-node flow goes back to "one curl on the
  laptop" instead of SSH pipe. SSH pipe can stay as a backup.
- `.claude/deployment-log.md`: new dated entry for the ticket migration
  (what was SSH-pipe era → what's ticket era, verification).
- `.claude/develop/install-ticket.md`: delete; story moves to
  deployment-log.
- `fleet-agent/README.md`: quickstart example can go back to
  `curl -fsSL "<install_url>" | bash`.

## Out of scope for v1

- **Invite codes for external install**: if you want non-admin users to
  add their own nodes (self-serve signup), you'd need a way to hand out
  `{label + ticket}` pairs. Could build a tiny web page on the hub that
  operators use; could bolt it onto this ticket system.
- **Expiry**: keep ticket indefinite for now. Add `expires_at` column
  later if threat model demands it.
- **Multi-use tickets**: if you want a "team install key" that multiple
  laptops can redeem, model it as a separate entity (InstallKey vs
  InstallTicket) — don't conflate.

## Why "ticket" and not "reuse NODE_TOKEN"

Tempting design: admin shares `NODE_TOKEN` with the laptop out-of-band;
laptop curls a public endpoint with `Authorization: Bearer <token>`.
Rejected because:

1. NODE_TOKEN is a long-lived credential; treating it as a query-string
   or even a header during install creates a leak window (shell history,
   browser history, server access logs). Ticket is one-use so logged
   values are useless.
2. Admin shouldn't need to see NODE_TOKEN at all — it should exist only
   on the node. Current `POST /nodes` does return it once, which is a
   minor concession for operational debugging; we can stop returning it
   entirely once the install flow doesn't need the admin to hand-carry it.
3. If NODE_TOKEN ever needs rotation, we want to swap it independently
   of any install artifact.
