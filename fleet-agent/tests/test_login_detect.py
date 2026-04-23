"""Tests for fleet_agent.login_detect."""

from __future__ import annotations

from typing import Any

import pytest

from fleet_agent import login_detect
from fleet_agent.runner import RunResult


@pytest.fixture
def fake_run(monkeypatch):
    """Patch login_detect.run_opencli with a callable controlled by tests."""
    mapping: dict[str, RunResult] = {}

    async def _run(_bin: str, *, site: str, **_kw: Any) -> RunResult:
        return mapping.get(
            site,
            RunResult(success=True, items=[], exit_code=0, duration_ms=10),
        )

    monkeypatch.setattr(login_detect, "run_opencli", _run)
    return mapping


async def test_detects_logged_in(fake_run):
    fake_run["zhihu"] = RunResult(success=True, items=[{}], exit_code=0, duration_ms=100)
    fake_run["xiaohongshu"] = RunResult(
        success=False, items=[], exit_code=77, duration_ms=50,
        error_code="AUTH_REQUIRED",
    )
    result = await login_detect.detect_logged_in_sites(
        "opencli", candidate_sites=["zhihu", "xiaohongshu", "bilibili"],
        timeout_sec=5,
    )
    assert "zhihu" in result
    assert "xiaohongshu" not in result
    assert "bilibili" in result  # falls through to default success


async def test_empty_result_treated_as_logged_in(fake_run):
    fake_run["weibo"] = RunResult(
        success=False, items=[], exit_code=66, duration_ms=30,
        error_code="EMPTY",
    )
    result = await login_detect.detect_logged_in_sites(
        "opencli", candidate_sites=["weibo"], timeout_sec=5,
    )
    assert "weibo" in result


async def test_unknown_site_skipped(fake_run):
    result = await login_detect.detect_logged_in_sites(
        "opencli", candidate_sites=["unknown_site"], timeout_sec=5,
    )
    # Unknown sites have no probe, so they default to assumed-logged-in.
    assert result == ["unknown_site"]


async def test_empty_candidate_list(fake_run):
    assert await login_detect.detect_logged_in_sites("opencli", candidate_sites=[]) == []
