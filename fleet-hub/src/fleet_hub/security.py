"""Hub-side security: token generation, output sanitization, audit log."""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fleet_hub.config import settings

logger = logging.getLogger(__name__)


def generate_token() -> str:
    """Generate a URL-safe node registration token."""
    return secrets.token_urlsafe(settings.node_token_bytes)


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
        return [sanitize(x) for x in obj]
    return obj


# ---------------------------------------------------------------------------
# Audit log — structured JSONL
# ---------------------------------------------------------------------------

_AUDIT_PATH: Path = settings.audit_log_path


def audit(event: str, **fields: Any) -> None:
    entry: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
    entry.update(fields)
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        logger.warning("audit log write failed: %s", _AUDIT_PATH, exc_info=True)
