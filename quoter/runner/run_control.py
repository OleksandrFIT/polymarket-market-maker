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

# Hard caps for the controlled live run — MINIMUM size (BTC-only).
# 5 shares/leg is Polymarket's floor → ~$5/window spend (cap $6, forward-looking
# so realized spend stays <= $6). Naked is bounded by max_naked + one in-flight
# fill, i.e. up to ~10 shares (~$5) worst case — a fill can land just past the cap.
CFG = Config(
    merge_edge=0.02, max_naked_shares=5, merge_levels=1,
    flat_size=5, per_market_cap_usd=6.0, min_time_to_expiry_sec=5.0,
    # phase-22 ladder (small first-live). per_window_cap=25 fits the full 5x5 ladder
    # notional (~$21.50); the REAL risk is bounded by naked_cap=10 (~$4.50 unhedged).
    ladder_anchor="entry", rungs=5, rung_size=5, rung_spacing=0.03,
    naked_cap=10, per_window_cap=25.0,
)
# Use continuous re-quoting (active two-sided market making) when trading.
REQUOTE = True


async def main() -> None:
    setup_logging("logs/control.log", "INFO")
    log = get_logger("run_control")
    creds = PolyCreds.from_env()
    state = TradingState()  # STOPPED by default
    runner = MergeRunner(creds, CFG, state, requote=REQUOTE)
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
