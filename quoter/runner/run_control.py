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
    # SMALL first auto-flat test (user-cautious): 2 rungs → up to 10 shares/side
    # (~$10/window), naked_cap=5 → ~$2.5 real risk, per_window_cap=15 fits it.
    # max_inflight_rungs=1 → staged posting: a fast crash sweeps at most 1 rung (5 sh),
    # not the whole ladder — fixes the live sweep that left 10 naked (−$3.75).
    # auto_flat=True: a naked leg that stands at naked_cap for flatten_grace_sec (5s, proactive=like-competitor)
    # is SOLD at market and that side is suppressed for the window — kills the naked
    # that lost 6/6 windows in the 2026-06-12 test. Grace lets choppy imbalances pair up.
    # ── DEEP-LADDER MEASUREMENT mode (2026-06-17) ──────────────────────────────
    # Real-tape sim (144 windows) proved: strategy = balanced pairs <$1 (same as guru),
    # but the ENTIRE gap is the cheap leg — guru fills it at ~$0.07, our naive top-of-book
    # at ~$0.32 (pair $1.02 → loses). This run MEASURES our REAL deep-catch fill price:
    # rest a static deep ladder in the cheap band 0.03..0.15 (7 rungs × $0.02) BOTH sides,
    # catch the loser's crash at our deep prices, log cheap-leg avg + pair cost per window.
    # Money bound = per_window_cap $10 (≈ max risk/window). complete_pairs balances at end.
    # PREV working near-mid cfg (revert): rungs=2, rung_spacing=0.03, naked_cap=5,
    #   per_window_cap=15, max_inflight_rungs=1, min_buy_price=0.42, trend_enabled=True.
    deep_ladder=True, deep_top=0.06,   # single rung lands at 0.05 (anchor - merge_edge/2)
    ladder_anchor="entry", rungs=1, rung_size=5, rung_spacing=0.02,
    naked_cap=60, per_window_cap=10.0, max_inflight_rungs=1,
    min_buy_price=0.03,   # deep band floor
    auto_flat=False, flatten_grace_sec=5.0,
    # 15m + near-end pair completion (2026-06-16): real on-chain data showed the 5m
    # ladder is structurally -EV (stuck-naked loser, 0/53), while the profitable
    # competitor runs 15m and COMPLETES pairs near the end. complete_pairs: in the
    # last complete_gate_sec, COMPLETE the pair if <$1 (taker the light leg) else
    # SELL the loser — never ride naked. 15m gives time for the 2nd leg to pair.
    assets=("BTC",),   # BTC-only for the first 15m test (clean single-market)
    timeframes=("15m",),
    complete_pairs=True, complete_gate_sec=120.0,
    # continuous completion: balance THROUGHOUT the window in small steps (10 sh) — cheaper
    # favorite, more retry time, never a single last-minute shot. SELL still near-end only.
    complete_continuous=True, complete_step=10,
    # guru-style: NEVER sell (3000/3000 BUY on his wallet). Hold the cheap deep residual to
    # resolution — removes the FOK-in-no-bid loss path and keeps the reversal-lottery upside.
    sell_fallback=False,
    # trend detector active the WHOLE window (not just last 90s): suppress the LOSING
    # side throughout so the bot never buys the falling knife in a trend.
    # trend detector OFF for the measurement: it would suppress the LOSING side's rungs,
    # but the losing (crashing) side is exactly the cheap leg whose fill price we measure.
    trend_enabled=False, trend_confidence=0.40, trend_gate_sec=600.0,
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
