"""Entry point: python -m fleet_hub"""

from __future__ import annotations

import uvicorn

from fleet_hub.config import settings


def main() -> None:
    uvicorn.run(
        "fleet_hub.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
