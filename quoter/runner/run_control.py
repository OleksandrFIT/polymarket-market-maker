"""Entry point: run the merge-maker runner + control dashboard forever.

  .venv/bin/python -m quoter.runner.run_control

Starts STOPPED — nothing trades until you press START on the dashboard
(http://127.0.0.1:8080, via SSH tunnel). BTC-only, hard caps.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv
from aiohttp import web

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from quoter.config import Config
from quoter.creds import PolyCreds
from quoter.ops.logger import get_logger, setup_logging
from quoter.runner.trading_state import TradingState
from quoter.runner.merge_runner import MergeRunner
from quoter.runner.control_dashboard import make_control_app

# Hard caps for the controlled live run (BTC-only, small size).
CFG = Config(
    merge_edge=0.01, max_naked_shares=10, merge_levels=1,
    flat_size=10, per_market_cap_usd=12.0, min_time_to_expiry_sec=5.0,
)


async def main() -> None:
    setup_logging("logs/control.log", "INFO")
    log = get_logger("run_control")
    creds = PolyCreds.from_env()
    state = TradingState()  # STOPPED by default
    runner = MergeRunner(creds, CFG, state)
    app = make_control_app(state, runner)

    apprunner = web.AppRunner(app)
    await apprunner.setup()
    site = web.TCPSite(apprunner, "127.0.0.1", 8080)
    await site.start()
    log.info("control_dashboard_up", url="http://127.0.0.1:8080", mode=state.mode)
    print("control dashboard: http://127.0.0.1:8080  (STOPPED — press START)")

    try:
        await runner.run_forever()
    finally:
        await apprunner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
