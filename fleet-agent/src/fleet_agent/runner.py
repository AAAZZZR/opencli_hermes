"""opencli subprocess runner.

Spawns `opencli <site> <command> ...` with a timeout, captures stdout/stderr,
parses the JSON output, and maps exit codes onto the error taxonomy
documented in OpenCLI's `src/errors.ts`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# From OpenCLI src/errors.ts — keep in sync.
_EXIT_CODE_MAP: dict[int, str] = {
    0: "OK",
    1: "GENERIC",
    2: "USAGE",
    66: "EMPTY",
    69: "SERVICE_UNAVAILABLE",
    75: "TIMEOUT",
    77: "AUTH_REQUIRED",
    78: "CONFIG",
    130: "INTERRUPTED",
}


@dataclass
class RunResult:
    success: bool
    items: list[dict[str, Any]]
    exit_code: int
    duration_ms: int
    error_code: str | None = None
    error_message: str | None = None
    stderr: str | None = None

    def to_frame(self, task_id: str) -> dict[str, Any]:
        """Shape as a WS 'result' frame for fleet-hub."""
        frame: dict[str, Any] = {
            "type": "result",
            "task_id": task_id,
            "success": self.success,
            "items": self.items,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
        }
        if not self.success:
            frame["error"] = {
                "code": self.error_code or "GENERIC",
                "message": self.error_message or "",
                "exit_code": self.exit_code,
                "stderr": (self.stderr or "")[:4096],
            }
        return frame


def build_argv(
    opencli_bin: str,
    *,
    site: str,
    command: str,
    args: dict[str, Any],
    positional_args: list[Any],
    format: str,
) -> list[str]:
    """Construct the argv for opencli invocation.

    Conventions:
      - positional_args are passed in order, before flags.
      - args dict entries are converted to CLI flags:
          True  → --flag
          False → skipped
          None  → skipped
          list  → --flag v1 --flag v2
          other → --flag <str(value)>
      - --format is always appended.
    """
    argv: list[str] = [opencli_bin, site, command]
    for p in positional_args:
        if p is None:
            continue
        argv.append(str(p))

    for key, value in args.items():
        flag = _flagify(key)
        if value is None or value is False:
            continue
        if value is True:
            argv.append(flag)
            continue
        if isinstance(value, list):
            for v in value:
                argv.append(flag)
                argv.append(str(v))
            continue
        argv.append(flag)
        argv.append(str(value))

    argv.extend(["--format", format or "json"])
    return argv


def _flagify(key: str) -> str:
    return "--" + key.replace("_", "-")


def _parse_stdout_items(stdout: str) -> list[dict[str, Any]]:
    """Best-effort parse of opencli JSON output into a list of item dicts."""
    stripped = stdout.strip()
    if not stripped:
        return []
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        # Not JSON — wrap raw text as one "item" to preserve debugging info.
        return [{"raw": stripped[:4096]}]

    if isinstance(data, list):
        return [item if isinstance(item, dict) else {"value": item} for item in data]
    if isinstance(data, dict):
        # Common: {"items": [...], "total": N, ...}
        if "items" in data and isinstance(data["items"], list):
            return [item if isinstance(item, dict) else {"value": item} for item in data["items"]]
        # Formal error envelope: {ok: false, error: {...}}
        if data.get("ok") is False and "error" in data:
            # Caller extracts the error separately; we return empty items.
            return []
        return [data]
    return [{"value": data}]


def _parse_error_envelope(stdout: str) -> dict[str, Any] | None:
    """If stdout contains an {ok:false, error:{...}} envelope, extract it."""
    stripped = stdout.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("ok") is False:
        err = data.get("error")
        if isinstance(err, dict):
            return err
    return None


async def run_opencli(
    opencli_bin: str,
    *,
    site: str,
    command: str,
    args: dict[str, Any],
    positional_args: list[Any],
    format: str = "json",
    timeout_sec: float = 120.0,
) -> RunResult:
    """Invoke opencli, return a RunResult."""
    argv = build_argv(
        opencli_bin,
        site=site, command=command,
        args=args, positional_args=positional_args, format=format,
    )
    logger.info("run: %s", " ".join(shlex.quote(p) for p in argv))

    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return RunResult(
            success=False, items=[], exit_code=-1, duration_ms=0,
            error_code="CONFIG",
            error_message=f"opencli binary not found: {opencli_bin}",
        )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        duration_ms = int((time.monotonic() - t0) * 1000)
        return RunResult(
            success=False, items=[], exit_code=75, duration_ms=duration_ms,
            error_code="TIMEOUT",
            error_message=f"opencli exceeded {timeout_sec}s timeout",
        )

    duration_ms = int((time.monotonic() - t0) * 1000)
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    exit_code = proc.returncode if proc.returncode is not None else -1

    if exit_code == 0:
        items = _parse_stdout_items(stdout)
        return RunResult(
            success=True, items=items, exit_code=0, duration_ms=duration_ms,
            stderr=stderr if stderr else None,
        )

    # Non-zero: prefer the stdout error envelope if present, else map exit code.
    envelope = _parse_error_envelope(stdout)
    if envelope is not None:
        return RunResult(
            success=False, items=[], exit_code=exit_code, duration_ms=duration_ms,
            error_code=envelope.get("code") or _EXIT_CODE_MAP.get(exit_code, "GENERIC"),
            error_message=envelope.get("message"),
            stderr=stderr,
        )

    return RunResult(
        success=False, items=[], exit_code=exit_code, duration_ms=duration_ms,
        error_code=_EXIT_CODE_MAP.get(exit_code, "GENERIC"),
        error_message=(stderr.strip().splitlines()[-1] if stderr.strip() else None),
        stderr=stderr,
    )
