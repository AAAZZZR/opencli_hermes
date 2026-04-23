"""REST and WS routers, mounted under /api/v1."""

from fastapi import APIRouter

from fleet_hub.api import health, install, nodes, tasks

router = APIRouter(prefix="/api/v1")
router.include_router(nodes.router)
router.include_router(tasks.router)
router.include_router(install.router)

top_level = APIRouter()
top_level.include_router(health.router)

__all__ = ["router", "top_level"]
