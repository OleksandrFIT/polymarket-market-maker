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
