"""Entry point: python -m fleet_mcp"""

import logging

from fleet_mcp.config import settings
from fleet_mcp.server import mcp

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

if __name__ == "__main__":
    mcp.run()
