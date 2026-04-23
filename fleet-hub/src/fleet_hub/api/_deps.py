"""Shared dependencies and helpers for API routes."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_hub.db import SessionLocal
from fleet_hub.models import Node


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _looks_like_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False


async def find_node(session: AsyncSession, ident: str) -> Node:
    """Resolve a node by id (UUID) or label. 404 if not found."""
    q = select(Node).where(Node.id == ident) if _looks_like_uuid(ident) \
        else select(Node).where(Node.label == ident)
    result = await session.execute(q)
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"node '{ident}' not found")
    return node


SessionDep = Depends(get_session)
