"""ORM models: Node, Task, Record."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fleet_hub.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    label: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    token: Mapped[str] = mapped_column(String(128))

    # Registered-at-handshake fields (updated every connect)
    mode: Mapped[str | None] = mapped_column(String(16), default=None)  # bridge|cdp
    os: Mapped[str | None] = mapped_column(String(16), default=None)
    logged_in_sites: Mapped[list[str]] = mapped_column(JSON, default=list)
    opencli_version: Mapped[str | None] = mapped_column(String(32), default=None)

    # Runtime status
    status: Mapped[str] = mapped_column(String(16), default="offline", index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    tasks: Mapped[list["Task"]] = relationship(back_populates="node", lazy="noload")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    node_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("nodes.id", ondelete="CASCADE"), index=True,
    )

    site: Mapped[str] = mapped_column(String(64), index=True)
    command: Mapped[str] = mapped_column(String(64), index=True)
    args: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    positional_args: Mapped[list[Any]] = mapped_column(JSON, default=list)
    format: Mapped[str] = mapped_column(String(16), default="json")
    timeout_sec: Mapped[int] = mapped_column(Integer, default=120)

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_message: Mapped[str | None] = mapped_column(String(2048), default=None)
    exit_code: Mapped[int | None] = mapped_column(Integer, default=None)

    items_total: Mapped[int] = mapped_column(Integer, default=0)   # agent returned
    items_stored: Mapped[int] = mapped_column(Integer, default=0)  # after dedup
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    node: Mapped[Node] = relationship(back_populates="tasks", lazy="joined")
    records: Mapped[list["Record"]] = relationship(back_populates="task", lazy="noload")


class Record(Base):
    __tablename__ = "records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), index=True,
    )

    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    normalized_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    task: Mapped[Task] = relationship(back_populates="records", lazy="noload")

    __table_args__ = (
        UniqueConstraint("task_id", "content_hash", name="uq_record_task_hash"),
        Index("ix_record_hash", "content_hash"),
    )
