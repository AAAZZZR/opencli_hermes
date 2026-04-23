"""Entry point: python -m fleet_agent"""

from __future__ import annotations

import asyncio
import logging
import signal

from fleet_agent.config import settings
from fleet_agent.ws_client import AgentClient


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(_amain())


async def _amain() -> None:
    client = AgentClient()
    loop = asyncio.get_running_loop()

    def _graceful(*_a):
        loop.create_task(client.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _graceful)
        except (NotImplementedError, ValueError):
            # Windows doesn't support add_signal_handler; KeyboardInterrupt is enough.
            pass

    await client.run()


if __name__ == "__main__":
    main()
