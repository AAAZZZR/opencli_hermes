"""Node CRUD + WebSocket endpoint."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_hub.api._deps import SessionDep, find_node, get_session
from fleet_hub.config import settings
from fleet_hub.db import SessionLocal
from fleet_hub.models import Node
from fleet_hub.schemas import (
    NodeCreate,
    NodeCreated,
    NodeOut,
    WSCollect,
    WSProgress,
    WSRegister,
    WSRegistered,
    WSResult,
)
from fleet_hub.security import audit, generate_token
from fleet_hub.ws.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nodes", tags=["nodes"])


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------

@router.post("", response_model=NodeCreated, status_code=status.HTTP_201_CREATED)
async def create_node(payload: NodeCreate, session: AsyncSession = SessionDep) -> NodeCreated:
    existing = await session.execute(select(Node).where(Node.label == payload.label))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"label '{payload.label}' already exists")
    node = Node(label=payload.label, token=generate_token())
    session.add(node)
    await session.flush()
    await session.refresh(node)
    audit("node.created", node_id=node.id, label=node.label)
    return NodeCreated.model_validate(node)


@router.get("", response_model=list[NodeOut])
async def list_nodes(session: AsyncSession = SessionDep) -> list[NodeOut]:
    result = await session.execute(select(Node).order_by(Node.created_at))
    nodes = list(result.scalars())
    # Reflect live WS state — status column may lag if hub was restarted.
    online_ids = manager.online_node_ids()
    for n in nodes:
        n.status = "online" if n.id in online_ids else "offline"
    return [NodeOut.model_validate(n) for n in nodes]


@router.get("/{ident}", response_model=NodeOut)
async def get_node(ident: str, session: AsyncSession = SessionDep) -> NodeOut:
    node = await find_node(session, ident)
    node.status = "online" if manager.is_online(node.id) else "offline"
    return NodeOut.model_validate(node)


@router.delete("/{ident}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(ident: str, session: AsyncSession = SessionDep) -> None:
    node = await find_node(session, ident)
    # Kick the live connection if any
    if manager.is_online(node.id):
        await manager.detach(node.id)
    await session.delete(node)
    audit("node.deleted", node_id=node.id, label=node.label)


# ---------------------------------------------------------------------------
# WebSocket endpoint — the agent connects here
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()

    # Expect a register frame as the very first message.
    try:
        first = await ws.receive_text()
    except WebSocketDisconnect:
        return

    try:
        frame = json.loads(first)
        register = WSRegister.model_validate(frame)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("invalid register frame: %s", exc)
        await ws.close(code=4002, reason="invalid_register_frame")
        return

    # Look up node by token.
    async with SessionLocal() as session:
        result = await session.execute(select(Node).where(Node.token == register.token))
        node = result.scalar_one_or_none()

        if node is None:
            audit("ws.reject", reason="invalid_token")
            await ws.close(code=4001, reason="invalid_token")
            return

        # Update registered metadata.
        node.mode = register.mode
        node.os = register.os
        node.logged_in_sites = list(register.logged_in_sites)
        node.opencli_version = register.opencli_version
        node.status = "online"
        node.last_seen_at = datetime.now(timezone.utc)
        await session.commit()
        node_id = node.id
        label = node.label

    await manager.attach(node_id, label, ws)
    await ws.send_json(WSRegistered(node_id=node_id, label=label).model_dump())
    audit("ws.connected", node_id=node_id, label=label)

    try:
        while True:
            msg = await ws.receive_text()
            await _handle_agent_message(node_id, label, ws, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("unexpected error on ws for node %s", label)
    finally:
        await manager.detach(node_id)
        async with SessionLocal() as session:
            result = await session.execute(select(Node).where(Node.id == node_id))
            node = result.scalar_one_or_none()
            if node is not None:
                node.status = "offline"
                node.last_seen_at = datetime.now(timezone.utc)
                await session.commit()
        audit("ws.disconnected", node_id=node_id, label=label)


async def _handle_agent_message(node_id: str, label: str, ws: WebSocket, raw: str) -> None:
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("non-json frame from %s", label)
        return

    ftype = frame.get("type")
    if ftype == "pong":
        return
    if ftype == "ping":
        await ws.send_json({"type": "pong"})
        return
    if ftype == "progress":
        try:
            WSProgress.model_validate(frame)
        except ValidationError:
            return
        # Progress is advisory — we don't currently persist it.
        return
    if ftype == "result":
        try:
            result = WSResult.model_validate(frame)
        except ValidationError as exc:
            logger.warning("invalid result frame from %s: %s", label, exc)
            return
        resolved = manager.resolve(node_id, result.task_id, frame)
        if not resolved:
            # Late result — the waiter is gone. Log for debugging.
            audit("ws.result.late", node_id=node_id, task_id=result.task_id)
        return

    logger.debug("ignoring unknown frame type %r from %s", ftype, label)


# Re-export for ws.manager's dispatch type hint
__all__ = ["router", "WSCollect"]
