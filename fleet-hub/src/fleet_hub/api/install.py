"""Install-script endpoint — serves the agent installer, pre-filled with
central URL and node token.

**Deployment note.** The rendered script embeds a per-node token. Expose
this endpoint only to trusted callers. The shipped `deploy/vps/Caddyfile`
blocks `/api/v1/nodes/install/*` from the public reverse proxy so tokens
never leak to anyone who guesses a label — run the installer fetch from
inside the VPS (`curl http://localhost:8031/...?label=X > agent.sh`) and
hand-carry the script to the laptop.
"""

from __future__ import annotations

import shlex
from importlib import resources

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_hub.api._deps import SessionDep, find_node
from fleet_hub.config import settings

router = APIRouter(tags=["install"])


def _load_template() -> str:
    """Load scripts/install-agent.sh shipped with the package."""
    # Files are force-included into fleet_hub/resources/ by pyproject.
    try:
        return (
            resources.files("fleet_hub.resources")
            .joinpath("install-agent.sh")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError):
        # Dev-mode: fall back to the source scripts/ directory.
        from pathlib import Path
        p = Path(__file__).resolve().parents[3] / "scripts" / "install-agent.sh"
        return p.read_text(encoding="utf-8")


def _render(template: str, *, central_url: str, token: str, label: str) -> str:
    # `shlex.quote` produces a bash-safe literal — either unchanged (simple
    # ASCII identifier) or single-quoted with embedded-quote escapes. The
    # template contains bare placeholders (not surrounded by quotes) so the
    # output of `shlex.quote` directly replaces them. This closes the naked
    # `.replace` injection vector if a malicious label ever slips past the
    # schema regex.
    return (
        template
        .replace("__CENTRAL_URL__", shlex.quote(central_url))
        .replace("__NODE_TOKEN__", shlex.quote(token))
        .replace("__NODE_LABEL__", shlex.quote(label))
        .replace("__OPENCLI_NPM_SPEC__", shlex.quote(settings.opencli_npm_spec))
        .replace("__FLEET_AGENT_INSTALL_SPEC__", shlex.quote(settings.fleet_agent_install_spec))
    )


@router.get("/nodes/install/agent.sh", response_class=PlainTextResponse)
async def install_script(
    label: str = Query(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    ),
    session: AsyncSession = SessionDep,
) -> str:
    """Return the bash installer for a given (already-registered) node label.

    Flow: admin calls `POST /nodes` to register + get a token, then curls
    `GET /nodes/install/agent.sh?label=<label>` to get the installer with
    the node's token pre-filled. Script is shell-safe (values quoted via
    `shlex.quote`) and labels are regex-validated to close the injection
    vector.
    """
    node = await find_node(session, label)
    tpl = _load_template()
    return _render(tpl, central_url=settings.public_url, token=node.token, label=node.label)
