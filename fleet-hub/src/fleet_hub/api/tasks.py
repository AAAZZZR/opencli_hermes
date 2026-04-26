"""Task dispatch and retrieval.

POST /tasks is the main entrypoint fleet-mcp uses. It accepts a node identifier
(label or id), a site+command+args, and synchronously awaits the agent's
result when wait=true (the default). On wait=false it returns immediately
with the task id and the caller can poll.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_hub.api._deps import SessionDep, find_node
from fleet_hub.config import settings
from fleet_hub.db import SessionLocal
from fleet_hub.models import Record, Task
from fleet_hub.pipeline import store_records
from fleet_hub.schemas import (
    RecordList,
    TaskCreate,
    TaskOut,
    TaskResult,
)
from fleet_hub.security import audit
from fleet_hub.ws.manager import NodeOffline, manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskResult)
async def create_task(payload: TaskCreate, session: AsyncSession = SessionDep) -> TaskResult:
    node = await find_node(session, payload.node_id)

    timeout = payload.timeout_sec or settings.default_task_timeout_sec
    timeout = min(timeout, settings.max_task_timeout_sec)

    task = Task(
        node_id=node.id,
        site=payload.site,
        command=payload.command,
        args=payload.args,
        positional_args=payload.positional_args,
        format=payload.format,
        timeout_sec=timeout,
        status="pending",
    )
    session.add(task)
    await session.flush()
    await session.refresh(task)

    if not payload.wait:
        # Fire-and-forget: schedule the dispatch on a background task and return.
        await session.commit()
        audit("task.created", task_id=task.id, node=node.label, site=task.site, command=task.command)
        asyncio.create_task(_dispatch_background(task.id))
        return _to_result(task, items=[])

    # Synchronous path — commit early so the background dispatch sees the row.
    await session.commit()

    result = await _dispatch_and_persist(task.id)
    return result


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    node_id: str | None = Query(default=None),
    site: str | None = Query(default=None),
    task_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = SessionDep,
) -> list[TaskOut]:
    q = select(Task).order_by(Task.created_at.desc()).limit(limit)
    if node_id:
        node = await find_node(session, node_id)
        q = q.where(Task.node_id == node.id)
    if site:
        q = q.where(Task.site == site)
    if task_status:
        q = q.where(Task.status == task_status)
    rows = (await session.execute(q)).scalars().all()
    return [TaskOut.model_validate(r) for r in rows]


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: str, session: AsyncSession = SessionDep) -> TaskOut:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"task {task_id} not found")
    return TaskOut.model_validate(task)


@router.get("/{task_id}/records", response_model=RecordList)
async def get_task_records(
    task_id: str,
    limit: int = Query(default=500, ge=1, le=5000),
    session: AsyncSession = SessionDep,
) -> RecordList:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"task {task_id} not found")
    rows = (
        await session.execute(
            select(Record)
            .where(Record.task_id == task_id)
            .order_by(Record.created_at)
            .limit(limit)
        )
    ).scalars().all()
    return RecordList(
        items=[r.normalized_data or r.raw_data for r in rows],
        total=len(rows),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _to_result(task: Task, *, items: list[dict]) -> TaskResult:
    return TaskResult.model_validate(task).model_copy(update={"items": items})


async def _dispatch_background(task_id: str) -> None:
    try:
        await _dispatch_and_persist(task_id)
    except Exception:
        logger.exception("background dispatch crashed for task %s", task_id)


async def _dispatch_and_persist(task_id: str) -> TaskResult:
    """Dispatch via WS, persist results, return the final TaskResult.

    Runs in its own session so it can commit independently of the request cycle.
    """
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise RuntimeError(f"task {task_id} vanished")

        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        await session.commit()

        frame = {
            "type": "collect",
            "task_id": task.id,
            "site": task.site,
            "command": task.command,
            "args": task.args,
            "positional_args": task.positional_args,
            "format": task.format,
            "timeout": task.timeout_sec,
        }

        t0 = time.monotonic()
        items: list[dict] = []
        try:
            # Dispatch WS frame and await agent's result frame.
            result_frame = await manager.dispatch(
                task.node_id, frame, timeout=float(task.timeout_sec),
            )
        except NodeOffline as exc:
            task.status = "failed"
            task.error_code = "NODE_OFFLINE"
            task.error_message = str(exc)
            task.finished_at = datetime.now(timezone.utc)
            task.duration_ms = int((time.monotonic() - t0) * 1000)
            await session.commit()
            audit("task.failed", task_id=task.id, reason="node_offline")
            return _to_result(task, items=[])
        except TimeoutError as exc:
            task.status = "timeout"
            task.error_code = "TIMEOUT"
            task.error_message = str(exc)
            task.finished_at = datetime.now(timezone.utc)
            task.duration_ms = int((time.monotonic() - t0) * 1000)
            await session.commit()
            audit("task.failed", task_id=task.id, reason="timeout")
            return _to_result(task, items=[])
        except Exception as exc:
            logger.exception("dispatch error for task %s", task.id)
            task.status = "failed"
            task.error_code = "DISPATCH_ERROR"
            task.error_message = str(exc)
            task.finished_at = datetime.now(timezone.utc)
            task.duration_ms = int((time.monotonic() - t0) * 1000)
            await session.commit()
            audit("task.failed", task_id=task.id, reason="dispatch_error")
            return _to_result(task, items=[])

        duration_ms = int((time.monotonic() - t0) * 1000)
        task.duration_ms = result_frame.get("duration_ms") or duration_ms

        err = result_frame.get("error") or {}
        # exit_code may be at top-level (successful runs) or nested under error.
        task.exit_code = result_frame.get("exit_code") or err.get("exit_code")

        success = bool(result_frame.get("success"))
        if not success:
            task.status = "failed"
            task.error_code = err.get("code") or "UNKNOWN"
            task.error_message = err.get("message")
            task.finished_at = datetime.now(timezone.utc)
            await session.commit()
            audit("task.failed", task_id=task.id, code=task.error_code)
            return _to_result(task, items=[])

        items = list(result_frame.get("items") or [])
        task.items_total = len(items)
        task.items_stored = await store_records(
            session,
            task_id=task.id,
            site=task.site,
            command=task.command,
            items=items,
        )
        task.status = "completed"
        task.finished_at = datetime.now(timezone.utc)
        await session.commit()
        audit(
            "task.completed",
            task_id=task.id,
            node_id=task.node_id,
            items_total=task.items_total,
            items_stored=task.items_stored,
            duration_ms=task.duration_ms,
        )

        # Pull stored records back as the returned items (post-normalization).
        rows = (
            await session.execute(
                select(Record).where(Record.task_id == task.id).order_by(Record.created_at)
            )
        ).scalars().all()
        inline_items = [r.normalized_data or r.raw_data for r in rows]
        return _to_result(task, items=inline_items)
