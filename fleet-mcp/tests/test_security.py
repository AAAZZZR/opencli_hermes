"""Tests for security module: whitelist, rate limiter, audit, sanitizer."""

import json
import time
from pathlib import Path
from unittest.mock import patch

from fleet_mcp.security import (
    RateLimiter,
    _hash_args,
    audit_log,
    check_whitelist,
    sanitize,
)


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

class TestWhitelist:
    def test_allowed_reads(self):
        # Classic reads, still work under deny-list model
        assert check_whitelist("xiaohongshu", "search") is None
        assert check_whitelist("zhihu", "hot") is None
        assert check_whitelist("reddit", "subreddit") is None

    def test_newly_allowed_reads(self):
        # Reads that the old allow-list rejected but we now permit
        assert check_whitelist("reddit", "read") is None          # read post + comments
        assert check_whitelist("reddit", "frontpage") is None
        assert check_whitelist("zhihu", "question") is None
        assert check_whitelist("twitter", "timeline") is None
        assert check_whitelist("facebook", "feed") is None        # facebook is now supported
        assert check_whitelist("arxiv", "search") is None         # new site

    def test_forbidden_global_command(self):
        err = check_whitelist("xiaohongshu", "eval")
        assert err is not None
        assert "globally forbidden" in err.lower()

    def test_unsupported_site(self):
        err = check_whitelist("totally-not-a-real-site", "search")
        assert err is not None
        assert "not supported" in err.lower()

    def test_blocked_write_on_known_site(self):
        # Write-type sub-commands must be blocked
        err = check_whitelist("zhihu", "answer")
        assert err is not None
        assert "blocked" in err.lower() or "write" in err.lower()

    def test_blocked_write_reddit(self):
        err = check_whitelist("reddit", "comment")
        assert err is not None
        err = check_whitelist("reddit", "upvote")
        assert err is not None

    def test_blocked_write_twitter(self):
        for verb in ("post", "reply", "follow", "like", "delete", "block"):
            err = check_whitelist("twitter", verb)
            assert err is not None, f"twitter {verb} should be blocked"

    def test_global_ban_beats_missing_site_entry(self):
        # A site with no entry in FORBIDDEN_PER_SITE should still reject global bans
        err = check_whitelist("arxiv", "shell")
        assert err is not None
        assert "globally forbidden" in err.lower()


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_allows_within_limit(self):
        rl = RateLimiter(per_node_rpm=60, global_rpm=120)
        for _ in range(3):
            assert rl.check("node-1") is None

    def test_per_node_burst_exceeded(self):
        rl = RateLimiter(per_node_rpm=60, global_rpm=600)
        # Burst is 3 for per-node
        for _ in range(3):
            assert rl.check("node-1") is None
        err = rl.check("node-1")
        assert err is not None
        assert "node" in err.lower()

    def test_global_burst_exceeded(self):
        # Global burst = max(3, global_rpm // 10)
        rl = RateLimiter(per_node_rpm=600, global_rpm=10)
        # Global burst = 3
        for i in range(3):
            assert rl.check(f"node-{i}") is None
        err = rl.check("node-99")
        assert err is not None
        assert "global" in err.lower()

    def test_different_nodes_independent(self):
        rl = RateLimiter(per_node_rpm=60, global_rpm=600)
        for _ in range(3):
            assert rl.check("node-a") is None
        # node-b should still have its own burst
        for _ in range(3):
            assert rl.check("node-b") is None


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_writes_jsonl(self, tmp_path):
        log_path = tmp_path / "audit.log"
        with patch("fleet_mcp.security._AUDIT_PATH", log_path):
            audit_log("dispatch", node_id="n1", site="zhihu", command="hot", result="ok")
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool"] == "dispatch"
        assert entry["node_id"] == "n1"
        assert entry["site"] == "zhihu"
        assert entry["result"] == "ok"
        assert "ts" in entry

    def test_args_hashed_not_raw(self, tmp_path):
        log_path = tmp_path / "audit.log"
        with patch("fleet_mcp.security._AUDIT_PATH", log_path):
            audit_log("dispatch", args={"q": "sensitive query"})
        entry = json.loads(log_path.read_text().strip())
        assert "args_hash" in entry
        assert entry["args_hash"].startswith("sha256:")
        assert "sensitive" not in json.dumps(entry)

    def test_hash_deterministic(self):
        h1 = _hash_args({"a": 1, "b": 2})
        h2 = _hash_args({"b": 2, "a": 1})
        assert h1 == h2


# ---------------------------------------------------------------------------
# Sanitizer
# ---------------------------------------------------------------------------

class TestSanitize:
    def test_strips_cookie(self):
        data = {"title": "ok", "cookie": "secret", "content": "hello"}
        result = sanitize(data)
        assert "title" in result
        assert "content" in result
        assert "cookie" not in result

    def test_strips_nested(self):
        data = {"user": {"name": "a", "session_token": "xxx"}, "items": [1]}
        result = sanitize(data)
        assert result["user"]["name"] == "a"
        assert "session_token" not in result["user"]
        assert result["items"] == [1]

    def test_strips_api_key_variants(self):
        data = {"api_key": "x", "apiKey": "y", "access_key": "z", "secret_key": "w"}
        result = sanitize(data)
        assert result == {}

    def test_strips_in_list_of_dicts(self):
        data = [{"id": 1, "token": "x"}, {"id": 2, "authorization": "y"}]
        result = sanitize(data)
        assert result == [{"id": 1}, {"id": 2}]

    def test_preserves_safe_fields(self):
        data = {"title": "hello", "count": 42, "tags": ["a", "b"]}
        assert sanitize(data) == data

    def test_handles_primitives(self):
        assert sanitize("hello") == "hello"
        assert sanitize(42) == 42
        assert sanitize(None) is None
