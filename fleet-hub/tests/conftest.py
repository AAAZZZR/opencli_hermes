"""Shared test fixtures.

Per-test database is a fresh SQLite file so tests don't interfere with each
other. The module-level engine is rebound to the test URL before any fleet_hub
module is imported.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Env var MUST be set before any fleet_hub import.
_TMP_DB = Path(tempfile.gettempdir()) / "fleet_hub_test.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["AUDIT_LOG_PATH"] = str(Path(tempfile.gettempdir()) / "fleet_hub_test_audit.log")
os.environ["PUBLIC_URL"] = "http://test.local"

import asyncio  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from fleet_hub.app import app  # noqa: E402
from fleet_hub.db import Base, engine  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db() -> AsyncIterator[None]:
    """Drop and recreate all tables before each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
