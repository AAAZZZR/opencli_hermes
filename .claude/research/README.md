# Research artifacts

Raw output from one-off research tasks that fed into code decisions. Kept
as an audit trail — the code is the source of truth; these are how we
arrived at it.

## `categorization-{A,B,C}.md` — opencli sub-command classification (2026-04-24)

Three parallel subagents ran `opencli <site> --help` for every site in
opencli v1.7.7 (101 sites total, split 34 / 34 / 33) and classified each
sub-command as READ (data retrieval, safe) or WRITE (modifies user
account / remote state, must be blocked when orchestrated by an LLM).

Output fed directly into `fleet-mcp/src/fleet_mcp/security.py`:
- `SUPPORTED_SITES` — flat frozenset of every site seen.
- `FORBIDDEN_PER_SITE` — the WRITE columns from these files.

See `.claude/deployment-log.md` (2026-04-24 security-flip entry) for the
methodology and ambiguity-resolution rules.

**Before editing `security.py`** to add/remove sites or commands, cross-
reference here so you know why something was classified a given way.
