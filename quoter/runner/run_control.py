"""Entry point: run the merge-maker runner + control dashboard forever.

  .venv/bin/python -m quoter.runner.run_control

LIVE TRADING IS DISABLED. This entry point is hardwired to dry_run=True — the bot
reads the live market and logs intended orders ("dryrun_place") but places NONE.
To ever re-enable real trading, dry_run below must be deliberately changed (and the
order-mutation wrappers in merge_runner are the only path that can hit the exchange).
Starts STOPPED — nothing happens until you press START on the dashboard
(http://127.0.0.1:8080, via SSH tunnel). BTC-only, hard caps.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from aiohttp import web

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
STRATEGY = os.environ.get("STRATEGY", "five_min").lower()

from quoter.config import Config
from quoter.creds import PolyCreds
from quoter.ops.logger import get_logger, setup_logging
from quoter.runner.trading_state import TradingState
from quoter.runner.merge_runner import MergeRunner
from quoter.runner.control_dashboard import make_control_app

if STRATEGY == "top_book":
    # LIVE_GO=1 (systemd drop-in, operator-set) lifts the lock for top_book ONLY —
    # first authorized attended test 2026-07-05 (cap $15/window, size 5). Default
    # (no env) stays dry-run. Remove the drop-in to re-lock.
    _LIVE_GO = os.environ.get("LIVE_GO") == "1"
    try:                                 # REQUOTE_SEC=1 for the 1s-vs-2s A/B; garbage -> 2.0
        _REQUOTE_SEC = float(os.environ.get("REQUOTE_SEC", "2") or "2")
        if _REQUOTE_SEC <= 0:
            _REQUOTE_SEC = 2.0
    except (TypeError, ValueError):
        _REQUOTE_SEC = 2.0
    # REGIME_GATE=0 -> DIRECTION-NEUTRAL mode (trade EVERY window like 0xb27b: gate off +
    # early-aggressive both-sided pairing so merges neutralize direction). Default (unset/1)
    # keeps the calm-only regime gate. Neutral relies on linked-pair + merge + completion/SELL
    # to stay balanced; naked risk in trends is the known unverified-without-live trade-off.
    _REGIME = os.environ.get("REGIME_GATE", "1") != "0"
    # neutral mode needs a bigger naked cap: skew_ok limits a single order to <= cap, and a
    # merge-maker holds more (balanced) inventory to pair up. Higher cap = more naked risk in a
    # trend (the trade-off of trading every window) — watchdog naked tripwire must be raised
    # before any neutral LIVE run (it's tuned to 8 for cap 6; dry-run doesn't use it).
    _cap = 6.0 if _REGIME else 12.0
    _early_sec = 0.0 if _REGIME else 60.0
    _early_size = min(0.0 if _REGIME else 10.0, _cap)   # size > cap => skew_ok blocks ALL early
    #                                                     quotes (silent no-quote) — clamp to cap
    # neutral leans into "always paired": merge EVERY pair immediately (recycle capital, like
    # 0xb27b) + complete profitable naked continuously (minimize time naked). SELL stays near-end.
    _merge_min = 5.0 if _REGIME else 1.0
    _continuous = not _REGIME
    CFG = Config(
        strategy="top_book", assets=("BTC",), timeframes=("5m",),
        tb_size=5.0, tb_naked_cap=_cap, tb_tick=0.001, tb_merge_min=_merge_min,
        requote_sec=_REQUOTE_SEC,
        regime_gate=_REGIME, regime_max_move_usd=25.0, regime_lookback_min=5,
        tb_early_sec=_early_sec, tb_early_size=_early_size,   # neutral: pair both legs early
        tb_complete=True, tb_complete_gate_sec=45.0,   # close naked pairs near window-end
        tb_complete_continuous=_continuous,            # neutral: complete <$1 legs all window
        complete_budget=6.0, tb_sell_naked=True,       # self-funding headroom; SELL loser in trend
        tb_link_margin=0.01,                           # linked-pair quoting: pairs < $1 by construction
        per_window_cap=15.0, per_market_cap_usd=15.0, min_time_to_expiry_sec=5.0,
        dry_run=not _LIVE_GO,            # LIVE only via explicit LIVE_GO=1
    )
elif STRATEGY == "five_min":
    CFG = Config(
        strategy="five_min", assets=("BTC",), timeframes=("5m",),
        lean=3, band_lo=0.62, band_hi=0.78, rung_size=5,
        per_window_cap=15.0, per_market_cap_usd=15.0, min_time_to_expiry_sec=5.0,
        dry_run=True,                    # LIVE DISABLED
    )
else:
    CFG = Config(
        assets=("BTC",), timeframes=("15m",),
        merge_edge=0.02, flat_size=5, rung_size=5, rungs=1, rung_spacing=0.03,
        ladder_anchor="entry", max_inflight_rungs=1, naked_cap=3, min_buy_price=0.20,
        deep_ladder=False, per_window_cap=15.0, per_market_cap_usd=15.0,
        min_time_to_expiry_sec=5.0, complete_pairs=True, complete_continuous=True,
        complete_step=10, complete_gate_sec=120.0, auto_flat=False, sell_fallback=False,
        trend_enabled=True, trend_confidence=0.35, trend_gate_sec=600.0,
        tilt_enabled=True, tilt_cutoff_sec=45.0, tilt_fee=0.02, tilt_max_price=0.90,
        tilt_frac=0.65, regime_window=30, regime_min_samples=12, regime_min_ev=0.0,
        dry_run=True,                    # LIVE DISABLED
    )
assert CFG.dry_run is True or (
    CFG.strategy == "top_book" and os.environ.get("LIVE_GO") == "1"
), "LIVE TRADING DISABLED: CFG.dry_run must stay True (top_book live needs LIVE_GO=1)"
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
