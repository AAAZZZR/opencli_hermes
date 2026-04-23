"""FastAPI app factory + lifespan."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fleet_hub import __version__
from fleet_hub.api import router as api_router
from fleet_hub.api import top_level as top_level_router
from fleet_hub.config import settings
from fleet_hub.db import init_db, shutdown_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    await init_db()
    yield
    await shutdown_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title="fleet-hub",
        version=__version__,
        description="Central hub for the OpenCLI fleet.",
        lifespan=lifespan,
    )
    app.include_router(top_level_router)
    app.include_router(api_router)
    return app


app = create_app()
