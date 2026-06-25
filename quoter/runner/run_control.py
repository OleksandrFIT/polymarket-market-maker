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

CFG = Config(
    assets=("BTC",), timeframes=("15m",),
    # ── base maker ladder (near-mid, two-sided): pairs <$1 + moderate insurance leg ──
    merge_edge=0.02, flat_size=5, rung_size=5, rungs=1, rung_spacing=0.03,
    ladder_anchor="entry", max_inflight_rungs=1,
    naked_cap=3,                    # bounds BASE imbalance (tilt may exceed this by design)
    min_buy_price=0.20,             # insurance allowed cheaper than 0.42, but not the
                                    # -EV deep tail ($0.05-0.10); calibrate vs -$61 hedge
    deep_ladder=False,              # OFF - the -EV deep-catch is gone
    # ── risk: ~$15/window ──
    per_window_cap=15.0, per_market_cap_usd=15.0, min_time_to_expiry_sec=5.0,
    # ── pair completion (continuous; never sell - guru-style). Completion is now
    #    tilt-aware in merge_runner: it won't pair off a deliberate favorite tilt. ──
    complete_pairs=True, complete_continuous=True, complete_step=10,
    complete_gate_sec=120.0, auto_flat=False, sell_fallback=False,
    # ── trend detector ON (drives the tilt only; base ladder is NEUTRAL) ──
    trend_enabled=True, trend_confidence=0.35, trend_gate_sec=600.0,
    # ── directional tilt + circuit-breaker. Calibrated via scripts/_replay_tilt.py:
    #    avg favorite entry 0.815, breakeven 0.835, gated paper-EV +0.048. regime_min_ev=0.0
    #    + window=30 loosen the CB so it pauses only on a genuinely -EV rolling stretch
    #    (at 0.01/20 it over-paused 29% of signals with no EV gain on pure-trend data). ──
    tilt_enabled=True, tilt_cutoff_sec=45.0, tilt_fee=0.02, tilt_max_price=0.90,
    tilt_frac=0.65,
    regime_window=30, regime_min_samples=12, regime_min_ev=0.0,
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
