"""Install-script endpoint — serves the agent installer, pre-filled with
central URL and node token.
"""

from __future__ import annotations

from importlib import resources

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_hub.api._deps import SessionDep, find_node
from fleet_hub.config import settings
from fleet_hub.models import Node

router = APIRouter(tags=["install"])


def _load_template() -> str:
    """Load scripts/install-agent.sh shipped with the package."""
    # Files are force-included into fleet_hub/resources/ by pyproject.
    try:
        return resources.files("fleet_hub.resources").joinpath("install-agent.sh").read_text()
    except (FileNotFoundError, ModuleNotFoundError):
        # Dev-mode: fall back to the source scripts/ directory.
        from pathlib import Path
        p = Path(__file__).resolve().parents[3] / "scripts" / "install-agent.sh"
        return p.read_text()


def _render(template: str, *, central_url: str, token: str, label: str) -> str:
    return (
        template
        .replace("__CENTRAL_URL__", central_url)
        .replace("__NODE_TOKEN__", token)
        .replace("__NODE_LABEL__", label)
        .replace("__OPENCLI_NPM_SPEC__", settings.opencli_npm_spec)
        .replace("__FLEET_AGENT_INSTALL_SPEC__", settings.fleet_agent_install_spec)
    )


@router.get("/nodes/install/agent.sh", response_class=PlainTextResponse)
async def install_script(
    label: str = Query(..., min_length=1, max_length=128),
    session: AsyncSession = SessionDep,
) -> str:
    """Return the bash installer for a given (already-registered) node label.

    Flow: admin calls `POST /nodes` to register + get a token, then calls
    `GET /nodes/install/agent.sh?label=<label>` from the laptop via curl.
    """
    node = await find_node(session, label)
    tpl = _load_template()
    return _render(tpl, central_url=settings.public_url, token=node.token, label=node.label)
