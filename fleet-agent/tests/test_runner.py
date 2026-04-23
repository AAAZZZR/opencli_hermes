"""Tests for fleet_agent.runner — subprocess mocked out."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from fleet_agent.runner import (
    _EXIT_CODE_MAP,
    RunResult,
    build_argv,
    run_opencli,
)


# ---------------------------------------------------------------------------
# build_argv
# ---------------------------------------------------------------------------

class TestBuildArgv:
    def test_site_command_format(self):
        argv = build_argv(
            "opencli", site="zhihu", command="hot",
            args={}, positional_args=[], format="json",
        )
        assert argv == ["opencli", "zhihu", "hot", "--format", "json"]

    def test_positional_args_before_flags(self):
        argv = build_argv(
            "opencli", site="zhihu", command="search",
            args={"limit": 10}, positional_args=["AI agents"], format="json",
        )
        assert argv == ["opencli", "zhihu", "search", "AI agents",
                        "--limit", "10", "--format", "json"]

    def test_bool_true_emits_flag(self):
        argv = build_argv(
            "opencli", site="zhihu", command="search",
            args={"verbose": True, "debug": False}, positional_args=[], format="json",
        )
        assert "--verbose" in argv
        assert "--debug" not in argv

    def test_none_values_skipped(self):
        argv = build_argv(
            "opencli", site="z", command="h",
            args={"limit": None, "q": "test"}, positional_args=[], format="json",
        )
        assert "--limit" not in argv

    def test_list_expands_repeated(self):
        argv = build_argv(
            "opencli", site="z", command="h",
            args={"tag": ["a", "b"]}, positional_args=[], format="json",
        )
        # --tag a --tag b appears in order
        assert argv.count("--tag") == 2
        i = argv.index("--tag")
        assert argv[i + 1] == "a"

    def test_underscore_becomes_dash(self):
        argv = build_argv(
            "opencli", site="z", command="h",
            args={"max_items": 5}, positional_args=[], format="json",
        )
        assert "--max-items" in argv

    def test_positional_none_skipped(self):
        argv = build_argv(
            "opencli", site="z", command="h",
            args={}, positional_args=[None, "x"], format="json",
        )
        assert argv.count("x") == 1
        assert None not in argv


# ---------------------------------------------------------------------------
# run_opencli — subprocess mocked via fake Process
# ---------------------------------------------------------------------------

class _FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int,
                 delay: float = 0.0, hang: bool = False) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._delay = delay
        self._hang = hang
        self._killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.Event().wait()  # hangs until cancelled
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self._killed = True

    async def wait(self) -> int:
        return self.returncode


@pytest.fixture
def fake_proc(monkeypatch):
    """Yields a setter function — test configures the fake, then calls run_opencli."""
    current: dict[str, Any] = {"proc": None, "not_found": False}

    async def _create(*_args, **_kwargs):
        if current["not_found"]:
            raise FileNotFoundError("opencli")
        assert current["proc"] is not None, "configure fake_proc first"
        return current["proc"]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create)
    return current


async def test_happy_path_items_array(fake_proc):
    fake_proc["proc"] = _FakeProcess(
        stdout=b'[{"id":"1","title":"A"},{"id":"2","title":"B"}]',
        stderr=b"",
        returncode=0,
    )
    result = await run_opencli(
        "opencli", site="zhihu", command="hot",
        args={}, positional_args=[], timeout_sec=5,
    )
    assert result.success
    assert len(result.items) == 2
    assert result.items[0]["title"] == "A"
    assert result.exit_code == 0


async def test_happy_path_items_object_with_items_key(fake_proc):
    fake_proc["proc"] = _FakeProcess(
        stdout=b'{"items":[{"x":1}],"total":1}', stderr=b"", returncode=0,
    )
    result = await run_opencli(
        "opencli", site="zhihu", command="hot",
        args={}, positional_args=[], timeout_sec=5,
    )
    assert result.success
    assert result.items == [{"x": 1}]


async def test_empty_stdout_ok(fake_proc):
    fake_proc["proc"] = _FakeProcess(stdout=b"", stderr=b"", returncode=0)
    result = await run_opencli(
        "opencli", site="z", command="h", args={}, positional_args=[], timeout_sec=5,
    )
    assert result.success
    assert result.items == []


async def test_auth_required_exit_77(fake_proc):
    fake_proc["proc"] = _FakeProcess(
        stdout=b"", stderr=b"AuthRequiredError: zhihu.com", returncode=77,
    )
    result = await run_opencli(
        "opencli", site="zhihu", command="hot",
        args={}, positional_args=[], timeout_sec=5,
    )
    assert not result.success
    assert result.error_code == "AUTH_REQUIRED"
    assert result.exit_code == 77


async def test_error_envelope_preferred_over_exit_code(fake_proc):
    fake_proc["proc"] = _FakeProcess(
        stdout=b'{"ok":false,"error":{"code":"RATE_LIMITED","message":"slow down"}}',
        stderr=b"", returncode=1,
    )
    result = await run_opencli(
        "opencli", site="z", command="h", args={}, positional_args=[], timeout_sec=5,
    )
    assert not result.success
    assert result.error_code == "RATE_LIMITED"
    assert result.error_message == "slow down"


async def test_unknown_exit_falls_back_to_generic(fake_proc):
    fake_proc["proc"] = _FakeProcess(stdout=b"", stderr=b"boom", returncode=42)
    result = await run_opencli(
        "opencli", site="z", command="h", args={}, positional_args=[], timeout_sec=5,
    )
    assert not result.success
    assert result.error_code not in _EXIT_CODE_MAP.values() or result.error_code == "GENERIC"
    # 42 isn't in the map, so it falls back to GENERIC:
    assert result.error_code == "GENERIC"


async def test_timeout_kills_process(fake_proc):
    fake_proc["proc"] = _FakeProcess(stdout=b"", stderr=b"", returncode=0, hang=True)
    result = await run_opencli(
        "opencli", site="z", command="h", args={}, positional_args=[], timeout_sec=0.1,
    )
    assert not result.success
    assert result.error_code == "TIMEOUT"
    assert result.exit_code == 75
    assert fake_proc["proc"]._killed is True


async def test_opencli_not_found(fake_proc):
    fake_proc["not_found"] = True
    result = await run_opencli(
        "opencli", site="z", command="h", args={}, positional_args=[], timeout_sec=5,
    )
    assert not result.success
    assert result.error_code == "CONFIG"


async def test_non_json_stdout_wrapped_as_raw(fake_proc):
    fake_proc["proc"] = _FakeProcess(stdout=b"hello there", stderr=b"", returncode=0)
    result = await run_opencli(
        "opencli", site="z", command="h", args={}, positional_args=[], timeout_sec=5,
    )
    assert result.success
    assert len(result.items) == 1
    assert "raw" in result.items[0]


# ---------------------------------------------------------------------------
# RunResult.to_frame
# ---------------------------------------------------------------------------

class TestToFrame:
    def test_success_frame(self):
        r = RunResult(success=True, items=[{"a": 1}], exit_code=0, duration_ms=100)
        f = r.to_frame("task-1")
        assert f["type"] == "result"
        assert f["task_id"] == "task-1"
        assert f["success"] is True
        assert f["items"] == [{"a": 1}]
        assert "error" not in f

    def test_failure_frame(self):
        r = RunResult(
            success=False, items=[], exit_code=77, duration_ms=50,
            error_code="AUTH_REQUIRED", error_message="logged out",
            stderr="some stderr",
        )
        f = r.to_frame("task-2")
        assert f["success"] is False
        assert f["error"]["code"] == "AUTH_REQUIRED"
        assert f["error"]["exit_code"] == 77
