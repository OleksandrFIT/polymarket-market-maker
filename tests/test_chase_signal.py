"""chase_signal: causal momentum favorite over the last lookback_sec. Pure — must
never read future ticks; the backtest feeds it only history seen so far."""
from quoter.research.chase import chase_signal


def test_rising_up_mid_returns_up():
    hist = [(0, 0.50), (20, 0.55), (40, 0.62)]
    assert chase_signal(hist, 40, 40, 0.05) == "Up"


def test_falling_up_mid_returns_down():
    hist = [(0, 0.50), (20, 0.46), (40, 0.42)]
    assert chase_signal(hist, 40, 40, 0.05) == "Down"


def test_flat_below_threshold_returns_none():
    hist = [(0, 0.50), (20, 0.51), (40, 0.52)]
    assert chase_signal(hist, 40, 40, 0.05) is None


def test_threshold_is_inclusive():
    hist = [(0, 0.50), (40, 0.55)]           # exactly +0.05
    assert chase_signal(hist, 40, 40, 0.05) == "Up"


def test_uses_sample_at_or_before_lookback_edge():
    # lookback 40s from now=100 -> edge at ts 60; the 0.50 at ts 60 is the baseline,
    # so 0.60 now is +0.10 -> Up (the older 0.30 at ts 0 must be ignored).
    hist = [(0, 0.30), (60, 0.50), (100, 0.60)]
    assert chase_signal(hist, 100, 40, 0.05) == "Up"


def test_short_history_uses_earliest_sample():
    hist = [(38, 0.50), (40, 0.60)]          # no sample as old as now-40
    assert chase_signal(hist, 40, 40, 0.05) == "Up"


def test_empty_history_returns_none():
    assert chase_signal([], 40, 40, 0.05) is None
