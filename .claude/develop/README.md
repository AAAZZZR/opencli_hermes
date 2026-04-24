# develop/

Parking lot for features that have been **designed but not built**. Each
`.md` in here is a future-work spec: enough detail that a future session
(human or Claude) can pick it up and implement without re-deriving the
decisions.

Difference from the other `.claude/` files:
- `spec.md` — architecture of what IS built, current state of truth.
- `deployment-log.md` — what happened in past sessions.
- `develop/` — designs for what MIGHT get built.

## Current contents

| File | What | Status | Rough effort |
|------|------|--------|--------------|
| [install-ticket.md](install-ticket.md) | One-time install URL so new nodes can install without SSH access to the VPS | Designed, not coded | ~80 LOC + small migration |

## When to promote a develop/ doc

Once a feature here gets built and merged, move its story into
`deployment-log.md` as a dated entry (what it addressed, what it
changed, verification) and delete the `develop/` doc — it's now redundant
with `spec.md` + git log.
