"""Guards the LIVE run_control.CFG values (defaults are tested separately in
test_config_tilt.py). Catches accidental reversion to the -EV deep-ladder config
or a mis-set risk cap / circuit-breaker tuning."""
import importlib
import os
import sys


def _load_rc(strategy: str):
    """Reload run_control with STRATEGY env set."""
    os.environ["STRATEGY"] = strategy
    mod_name = "quoter.runner.run_control"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    mod = importlib.import_module(mod_name)
    return mod


def test_five_min_cfg_is_dry_run():
    # the DEFAULT strategy is five_min — assert its live-lock explicitly
    rc = _load_rc("five_min")
    c = rc.CFG
    assert c.dry_run is True          # LIVE DISABLED
    assert c.strategy == "five_min"
    assert c.timeframes == ("5m",)


def test_live_cfg_is_momentum_tilt_step1():
    rc = _load_rc("tilt")
    c = rc.CFG
    # the -EV deep-ladder measurement mode must be OFF
    assert c.deep_ladder is False
    # three-component strategy enabled
    assert c.trend_enabled is True
    assert c.tilt_enabled is True
    assert c.complete_pairs is True
    assert c.sell_fallback is False          # never sell (guru-style)
    # risk bound: ~$15/window, BTC 15m only
    assert c.per_window_cap == 15.0
    assert c.per_market_cap_usd == 15.0
    assert c.assets == ("BTC",)
    assert c.timeframes == ("15m",)
    # circuit-breaker tuning calibrated via _replay_tilt
    assert c.regime_min_ev == 0.0
    assert c.regime_window == 30
    assert c.tilt_max_price == 0.90
    assert c.tilt_frac == 0.65
    assert c.rungs == 1
    assert c.naked_cap == 3
    assert c.trend_confidence == 0.35
    # LIVE TRADING DISABLED — entry point is hardwired to dry-run only
    assert c.dry_run is True


def test_top_book_cfg_is_dry_run_by_default():
    os.environ.pop("LIVE_GO", None)
    rc = _load_rc("top_book")
    c = rc.CFG
    assert c.dry_run is True                  # LIVE DISABLED without explicit LIVE_GO=1
    assert c.strategy == "top_book"
    assert c.timeframes == ("5m",)
    assert c.tb_size == 5.0
    assert c.tb_naked_cap == 6.0              # tightened from 10 -> франшиза у ворожому тренді ~-$2.4
    assert c.requote_sec == 2.0               # default cadence; env REQUOTE_SEC overrides
    assert c.regime_gate is True              # auto-skip trending windows (pair-maker loses in trend)
    assert c.regime_max_move_usd == 25.0
    assert c.regime_lookback_min == 5
    assert c.tb_complete is True              # near-end pair completion (zero naked residual)
    assert c.tb_complete_gate_sec == 45.0
    assert c.tb_link_margin == 0.01           # linked-pair quoting (pairs < $1 by construction)
    assert c.tb_early_sec == 0.0              # default mode: no early-aggressive phase (unchanged)
    assert c.complete_budget == 6.0           # self-funding headroom above the $15 maker cap
    assert c.tb_sell_naked is True            # SELL the loser when pair >= $1 (trend), not ride
    assert c.per_window_cap == 15.0           # проба-пера cap


def test_top_book_clock_only_mode_via_env():
    # REGIME_GATE=0 selects the clock-only chop tactic, wired to the EXACT config the offline sim
    # validated (cap 6 / size 5 / no early phase / near-end-only completion) so the live measurement
    # matches the pre-registered thresholds — NOT the old 0xb27b-neutral bundle (cap 12 / early-10).
    os.environ["REGIME_GATE"] = "0"
    try:
        rc = _load_rc("top_book")
        assert rc.CFG.regime_gate is False        # regime gate off; chop_gate handles trends instead
        assert rc.CFG.chop_gate is True
        assert rc.CFG.chop_trend_revoke is False  # clock-only (calib winner); detector observe-only
        assert rc.CFG.tb_naked_cap == 6.0         # validated cap; watchdog NAKED_LIMIT=8 assumes it
        assert rc.CFG.tb_early_sec == 0.0         # no early-aggressive phase (sim ran constant size 5)
        assert rc.CFG.tb_early_size == 0.0
        assert rc.CFG.tb_merge_min == 1.0         # merge EVERY pair immediately (recycle capital)
        assert rc.CFG.tb_complete_continuous is False  # completion NEAR-END only (matches sim)
        assert rc.CFG.tb_link_margin == 0.01      # linked-pair + completion + SELL kept
        assert rc.CFG.tb_sell_naked is True
        assert rc.CFG.dry_run is True             # NEVER lifts the live lock (needs LIVE_GO=1)
    finally:
        os.environ.pop("REGIME_GATE", None)


def test_top_book_default_is_gated_not_neutral():
    os.environ.pop("REGIME_GATE", None)
    rc = _load_rc("top_book")
    assert rc.CFG.regime_gate is True and rc.CFG.tb_early_sec == 0.0  # unchanged default
    assert rc.CFG.tb_merge_min == 5.0 and rc.CFG.tb_complete_continuous is False  # default unchanged


def test_top_book_requote_sec_from_env():
    os.environ["REQUOTE_SEC"] = "1"
    try:
        rc = _load_rc("top_book")
        assert rc.CFG.requote_sec == 1.0      # env overrides the 2.0 default (1s A/B)
        assert rc.CFG.dry_run is True         # cadence knob never lifts the live lock
    finally:
        os.environ.pop("REQUOTE_SEC", None)


def test_top_book_requote_sec_bad_env_falls_back():
    os.environ["REQUOTE_SEC"] = "not-a-number"
    try:
        rc = _load_rc("top_book")
        assert rc.CFG.requote_sec == 2.0      # garbage env -> safe default, no crash
    finally:
        os.environ.pop("REQUOTE_SEC", None)


def test_top_book_live_go_lifts_lock_only_here():
    os.environ["LIVE_GO"] = "1"
    try:
        rc = _load_rc("top_book")
        assert rc.CFG.dry_run is False        # explicit operator-set env lifts the lock
        assert rc.CFG.per_window_cap == 15.0  # live test capped at $15/window
        # other strategies stay HARD-locked even with LIVE_GO=1
        rc5 = _load_rc("five_min")
        assert rc5.CFG.dry_run is True
    finally:
        os.environ.pop("LIVE_GO", None)


def test_momentum_cfg_is_dry_run_by_default():
    os.environ.pop("LIVE_GO", None)
    rc = _load_rc("momentum")
    c = rc.CFG
    assert c.dry_run is True               # LIVE DISABLED without LIVE_GO
    assert c.strategy == "momentum"
    assert c.timeframes == ("5m",)
    assert c.tb_size == 5.0
    assert c.mom_residual_cap == 5.0
    assert c.mom_chase_max == 0.95
    assert c.per_window_cap == 15.0


def test_momentum_live_go_lifts_lock():
    os.environ["LIVE_GO"] = "1"
    try:
        assert _load_rc("momentum").CFG.dry_run is False
        assert _load_rc("five_min").CFG.dry_run is True   # others stay locked
    finally:
        os.environ.pop("LIVE_GO", None)
