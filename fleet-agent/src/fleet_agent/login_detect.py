"""At-register-time probe for logged-in sites.

Given a list of candidate sites, run a short low-cost opencli command for each
and report which ones didn't come back with AUTH_REQUIRED (exit 77).

For Phase 1 we only probe a small built-in list — the fleet-hub's whitelist
will normally be the authoritative list. For sites that don't have a cheap
public command, we fall back to `opencli <site>` with no args (which usually
prints help and exits 0 or 2, neither of which is AUTH_REQUIRED).
"""

from __future__ import annotations

import asyncio
import logging

from fleet_agent.runner import run_opencli

logger = logging.getLogger(__name__)


# (site, command, args, positional_args) — cheap probes per site.
_PROBES: dict[str, tuple[str, dict, list]] = {
    "xiaohongshu": ("search", {"limit": 1}, ["test"]),
    "zhihu":       ("hot",    {"limit": 1}, []),
    "bilibili":    ("hot",    {"limit": 1}, []),
    "weibo":       ("hot",    {"limit": 1}, []),
    "twitter":     ("timeline", {"limit": 1}, []),
    "reddit":      ("hot",    {"limit": 1}, []),
}


async def probe_site(opencli_bin: str, site: str, *, timeout_sec: float) -> bool:
    """Run a lightweight probe. Return True if the site appears to be logged in."""
    probe = _PROBES.get(site)
    if probe is None:
        # No known probe — skip detection (assume logged in, conservatively).
        return True
    command, args, positional = probe
    result = await run_opencli(
        opencli_bin,
        site=site, command=command,
        args=args, positional_args=list(positional),
        format="json", timeout_sec=timeout_sec,
    )
    if result.success:
        return True
    # AUTH_REQUIRED (77) is the strong negative signal.
    if result.error_code == "AUTH_REQUIRED":
        return False
    # EMPTY (66) means not logged out — probably fine but possibly rate-limited.
    # Conservative: treat as logged in (avoid false negatives that would drop
    # a usable node from the fleet).
    if result.error_code == "EMPTY":
        return True
    # TIMEOUT or SERVICE_UNAVAILABLE → we don't know; default to true so the
    # admin UI can still pick this node.
    logger.info("probe %s returned %s — keeping as logged-in", site, result.error_code)
    return True


async def detect_logged_in_sites(
    opencli_bin: str,
    *,
    candidate_sites: list[str],
    timeout_sec: float = 10.0,
    concurrency: int = 3,
) -> list[str]:
    """Probe each candidate in parallel (bounded). Return the ones that pass."""
    if not candidate_sites:
        return []

    sem = asyncio.Semaphore(concurrency)

    async def _one(site: str) -> tuple[str, bool]:
        async with sem:
            return site, await probe_site(opencli_bin, site, timeout_sec=timeout_sec)

    results = await asyncio.gather(*[_one(s) for s in candidate_sites])
    return [s for s, ok in results if ok]
