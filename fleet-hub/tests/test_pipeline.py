"""Unit tests for pipeline: normalize + content_hash + store_records."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from fleet_hub.db import SessionLocal
from fleet_hub.models import Node, Record, Task
from fleet_hub.pipeline import content_hash, normalize_item, store_records
from fleet_hub.security import generate_token


class TestNormalizeItem:
    def test_picks_aliased_keys(self):
        item = {"name": "Title A", "link": "https://x", "body": "Content"}
        n = normalize_item(item)
        assert n["title"] == "Title A"
        assert n["url"] == "https://x"
        assert n["content"] == "Content"

    def test_unknown_fields_kept_in_extra(self):
        item = {"title": "t", "custom_field": 42}
        n = normalize_item(item)
        assert n["extra"] == {"custom_field": 42}

    def test_non_dict_item_wrapped(self):
        n = normalize_item("just a string")
        assert n["content"] == "just a string"
        assert n["title"] is None

    def test_empty_or_none_skipped(self):
        item = {"title": "", "name": "fallback"}
        n = normalize_item(item)
        assert n["title"] == "fallback"


class TestContentHash:
    def test_deterministic(self):
        n = {"id": "1", "title": "T", "url": "U", "content": "C"}
        assert content_hash("site", "cmd", n) == content_hash("site", "cmd", n)

    def test_different_site_different_hash(self):
        n = {"id": "1", "title": "T", "url": "U", "content": "C"}
        assert content_hash("a", "x", n) != content_hash("b", "x", n)

    def test_fallback_on_empty_fields(self):
        n = {"id": None, "title": None, "url": None, "content": None, "extra": {"custom": 1}}
        h = content_hash("site", "cmd", n)
        assert len(h) == 64  # sha256 hex


async def _create_test_task() -> str:
    async with SessionLocal() as s:
        n = Node(label="test-n", token=generate_token())
        s.add(n)
        await s.flush()
        t = Task(node_id=n.id, site="zhihu", command="hot")
        s.add(t)
        await s.commit()
        return t.id


async def test_store_records_inserts_new():
    task_id = await _create_test_task()
    async with SessionLocal() as s:
        count = await store_records(
            s,
            task_id=task_id,
            site="zhihu",
            command="hot",
            items=[
                {"id": "a", "title": "First"},
                {"id": "b", "title": "Second"},
            ],
        )
        await s.commit()
    assert count == 2

    async with SessionLocal() as s:
        rows = (await s.execute(select(Record).where(Record.task_id == task_id))).scalars().all()
        assert len(rows) == 2


async def test_store_records_dedups_intra_batch():
    task_id = await _create_test_task()
    async with SessionLocal() as s:
        count = await store_records(
            s,
            task_id=task_id, site="zhihu", command="hot",
            items=[
                {"id": "a", "title": "First"},
                {"id": "a", "title": "First"},  # dup
            ],
        )
        await s.commit()
    assert count == 1


async def test_store_records_dedups_vs_existing():
    task_id = await _create_test_task()
    async with SessionLocal() as s:
        await store_records(s, task_id=task_id, site="zhihu", command="hot",
                            items=[{"id": "a", "title": "First"}])
        await s.commit()
    async with SessionLocal() as s:
        count = await store_records(s, task_id=task_id, site="zhihu", command="hot",
                                     items=[{"id": "a", "title": "First"},
                                            {"id": "b", "title": "New"}])
        await s.commit()
    assert count == 1

    async with SessionLocal() as s:
        rows = (await s.execute(select(Record).where(Record.task_id == task_id))).scalars().all()
        assert len(rows) == 2


async def test_store_records_strips_sensitive_fields():
    task_id = await _create_test_task()
    async with SessionLocal() as s:
        await store_records(s, task_id=task_id, site="zhihu", command="hot", items=[
            {"id": "a", "title": "T", "cookie": "bad", "access_key": "bad"},
        ])
        await s.commit()
    async with SessionLocal() as s:
        rec = (await s.execute(select(Record).where(Record.task_id == task_id))).scalar_one()
        assert "cookie" not in rec.raw_data
        assert "access_key" not in rec.raw_data
        assert rec.raw_data["title"] == "T"


async def test_store_records_empty_items_returns_zero():
    task_id = await _create_test_task()
    async with SessionLocal() as s:
        count = await store_records(s, task_id=task_id, site="zhihu", command="hot", items=[])
    assert count == 0
