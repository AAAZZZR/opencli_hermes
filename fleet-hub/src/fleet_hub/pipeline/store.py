"""Dedup + persist records belonging to a task."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_hub.models import Record
from fleet_hub.pipeline.normalize import content_hash, normalize_item
from fleet_hub.security import sanitize


async def store_records(
    session: AsyncSession,
    *,
    task_id: str,
    site: str,
    command: str,
    items: list[Any],
) -> int:
    """Sanitize → normalize → dedup → insert. Returns number of rows inserted.

    Dedup is per-(task, content_hash). Across tasks the same content can recur
    (e.g. same Zhihu post seen by two accounts) — that's intentional; task-level
    uniqueness is what the dedup prevents.
    """
    if not items:
        return 0

    prepared: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    seen_hashes: set[str] = set()
    for raw in items:
        clean_raw = sanitize(raw) if isinstance(raw, (dict, list)) else raw
        normalized = normalize_item(clean_raw)
        h = content_hash(site, command, normalized)
        if h in seen_hashes:
            continue  # intra-batch dedup
        seen_hashes.add(h)
        prepared.append((
            h,
            clean_raw if isinstance(clean_raw, dict) else {"value": clean_raw},
            normalized,
        ))

    if not prepared:
        return 0

    # Query which hashes already exist for this task.
    existing_q = await session.execute(
        select(Record.content_hash).where(
            Record.task_id == task_id,
            Record.content_hash.in_([h for h, _, _ in prepared]),
        )
    )
    existing = {row[0] for row in existing_q.all()}

    new_rows = [
        Record(
            task_id=task_id,
            content_hash=h,
            raw_data=raw,
            normalized_data=norm,
        )
        for h, raw, norm in prepared
        if h not in existing
    ]
    if not new_rows:
        return 0

    session.add_all(new_rows)
    await session.flush()
    return len(new_rows)
