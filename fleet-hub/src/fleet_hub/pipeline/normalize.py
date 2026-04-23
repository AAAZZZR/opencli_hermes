"""Item normalization and content hashing.

Different site adapters emit slightly different field names. We map a small
set of aliased keys onto a standard skeleton so downstream storage and query
can be uniform.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_TITLE_KEYS = ("title", "name", "heading")
_URL_KEYS = ("url", "link", "href", "permalink")
_CONTENT_KEYS = ("content", "text", "body", "description", "excerpt", "summary")
_AUTHOR_KEYS = ("author", "user", "uploader", "creator", "username", "owner")
_DATE_KEYS = ("published_at", "created_at", "date", "pub_date", "posted_at", "timestamp")
_ID_KEYS = ("id", "item_id", "note_id", "tweet_id", "post_id", "question_id")


def _pick(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def normalize_item(item: Any) -> dict[str, Any]:
    """Produce a normalized skeleton from an arbitrary item.

    Non-dict items are wrapped as {"value": item}. Unknown fields are not dropped
    — they're preserved under 'extra' so callers don't lose data.
    """
    if not isinstance(item, dict):
        return {
            "id": None, "title": None, "url": None, "content": str(item),
            "author": None, "published_at": None, "extra": {},
        }

    known = set(_TITLE_KEYS + _URL_KEYS + _CONTENT_KEYS + _AUTHOR_KEYS + _DATE_KEYS + _ID_KEYS)
    normalized = {
        "id": _pick(item, _ID_KEYS),
        "title": _pick(item, _TITLE_KEYS),
        "url": _pick(item, _URL_KEYS),
        "content": _pick(item, _CONTENT_KEYS),
        "author": _pick(item, _AUTHOR_KEYS),
        "published_at": _pick(item, _DATE_KEYS),
        "extra": {k: v for k, v in item.items() if k not in known},
    }
    return normalized


def content_hash(site: str, command: str, normalized: dict[str, Any]) -> str:
    """SHA-256 of (site|command|id|title|url|content). Hex digest.

    Keyed on (site, command) so the same URL scraped via different commands still
    produces distinct records. Falls back to hashing the full item if all
    identifying fields are empty.
    """
    parts = [
        site or "",
        command or "",
        str(normalized.get("id") or ""),
        str(normalized.get("title") or ""),
        str(normalized.get("url") or ""),
        str(normalized.get("content") or "")[:4096],  # cap to avoid pathological huge hashes
    ]
    key = "|".join(parts)
    if not any(p for p in parts[2:]):
        # All identifying fields empty — hash the full payload as a last resort.
        key = site + "|" + command + "|" + json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.sha256(key.encode()).hexdigest()
