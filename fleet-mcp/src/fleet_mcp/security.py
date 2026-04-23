"""Security layer: command whitelist, rate limiting, audit log, output sanitization."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fleet_mcp.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command whitelist
# ---------------------------------------------------------------------------

SUPPORTED_SITES: dict[str, set[str]] = {
    "xiaohongshu": {"search", "note", "feed", "user"},
    "zhihu": {"hot", "search", "question"},
    "bilibili": {"hot", "search", "ranking"},
    "weibo": {"hot", "search"},
    "twitter": {"search", "timeline", "profile"},
    "reddit": {"hot", "subreddit", "search"},
}

SITE_DESCRIPTIONS: dict[str, str] = {
    "xiaohongshu": "Xiaohongshu (RedNote) — search and read posts",
    "zhihu": "Zhihu — Chinese Q&A platform",
    "bilibili": "Bilibili — Chinese video platform",
    "weibo": "Weibo — Chinese microblogging platform",
    "twitter": "Twitter/X — microblogging platform",
    "reddit": "Reddit — community forums",
}

FORBIDDEN_COMMANDS: set[str] = {
    "browser",    # any browser.<sub> — `eval`/`click`/`type` can all inject or mutate
    "eval",       # explicit JS exec in page context
    "register",   # installs arbitrary external binaries
    "install",    # auto-runs brew / apt to install packages
    "plugin",     # installs GitHub packages as adapter plugins
    "daemon",     # installs the bridge daemon as a system service
    "adapter",    # adapter eject/reset/mutation
    "synthesize", # writes adapter code from capture data
    "generate",   # writes files from network captures
    "record",     # cross-tab XHR/fetch injection recorder
    "exec",       # generic exec
    "shell",      # generic shell
}


def check_whitelist(site: str, command: str) -> str | None:
    """Return an error message if (site, command) is not allowed, else None."""
    if command in FORBIDDEN_COMMANDS:
        return f"Command '{command}' is explicitly forbidden"
    commands = SUPPORTED_SITES.get(site)
    if commands is None:
        allowed = ", ".join(sorted(SUPPORTED_SITES))
        return f"Site '{site}' is not supported. Allowed: {allowed}"
    if command not in commands:
        allowed = ", ".join(sorted(commands))
        return f"Command '{command}' is not allowed for site '{site}'. Allowed: {allowed}"
    return None


# ---------------------------------------------------------------------------
# Rate limiter — token bucket, in-memory
# ---------------------------------------------------------------------------

class _TokenBucket:
    """Simple token-bucket rate limiter."""

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate  # tokens per second
        self._burst = burst
        self._tokens = float(burst)
        self._last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class RateLimiter:
    """Per-node + global rate limiting."""

    def __init__(
        self,
        per_node_rpm: int = settings.rate_limit_per_node,
        global_rpm: int = settings.rate_limit_global,
    ) -> None:
        self._per_node_rpm = per_node_rpm
        self._global = _TokenBucket(rate=global_rpm / 60.0, burst=max(3, global_rpm // 10))
        self._nodes: dict[str, _TokenBucket] = {}

    def _get_node_bucket(self, node_id: str) -> _TokenBucket:
        if node_id not in self._nodes:
            self._nodes[node_id] = _TokenBucket(
                rate=self._per_node_rpm / 60.0,
                burst=3,
            )
        return self._nodes[node_id]

    def check(self, node_id: str) -> str | None:
        """Return error message if rate limit exceeded, else None."""
        if not self._global.allow():
            return "Global rate limit exceeded"
        if not self._get_node_bucket(node_id).allow():
            return f"Rate limit exceeded for node '{node_id}'"
        return None


rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# Audit log — JSONL, args hashed, daily rotation
# ---------------------------------------------------------------------------

_AUDIT_PATH: Path = settings.audit_log_path


def _hash_args(args: dict[str, Any] | None) -> str:
    raw = json.dumps(args, sort_keys=True, default=str) if args else ""
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def audit_log(
    tool: str,
    *,
    node_id: str | None = None,
    site: str | None = None,
    command: str | None = None,
    args: dict[str, Any] | None = None,
    result: str = "ok",
    duration_ms: int | None = None,
    items_count: int | None = None,
) -> None:
    """Append one JSONL line to the audit log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
    }
    if node_id:
        entry["node_id"] = node_id
    if site:
        entry["site"] = site
    if command:
        entry["command"] = command
    if args is not None:
        entry["args_hash"] = _hash_args(args)
    entry["result"] = result
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if items_count is not None:
        entry["items_count"] = items_count

    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        logger.warning("Failed to write audit log to %s", _AUDIT_PATH, exc_info=True)


# ---------------------------------------------------------------------------
# Output sanitization — strip sensitive fields recursively
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERN = re.compile(
    r"(cookie|session|token|x[-_]csrf[-_]token|authorization|"
    r"(api|access|secret)[-_]?key)",
    re.IGNORECASE,
)


def sanitize(obj: Any) -> Any:
    """Recursively strip fields whose names match sensitive patterns."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items() if not _SENSITIVE_PATTERN.search(k)}
    if isinstance(obj, list):
        return [sanitize(item) for item in obj]
    return obj
