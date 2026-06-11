"""Pure trend detector: win-prob, vol estimate, bias decision."""

from quoter.config import Config
from quoter.runner.trend_detector import detect_bias, sigma_remaining, win_prob_up


def cfg(**kw):
    base = dict(trend_enabled=True, trend_confidence=0.35, trend_buffer_sec=60.0,
                trend_vol_fallback=30.0, trend_stale_sec=10.0, trend_gate_sec=90.0)
    base.update(kw)
    return Config(**base)


def test_win_prob_at_strike_is_half():
    assert abs(win_prob_up(100000.0, 100000.0, 20.0) - 0.5) < 1e-9


def test_win_prob_far_above_and_below():
    assert win_prob_up(100100.0, 100000.0, 20.0) > 0.99
    assert win_prob_up(99900.0, 100000.0, 20.0) < 0.01


def test_bias_up_when_clearly_above():
    assert detect_bias(100050.0, 100000.0, 20.0, 30.0, cfg()) == "UP"


def test_bias_down_when_clearly_below():
    assert detect_bias(99950.0, 100000.0, 20.0, 30.0, cfg()) == "DOWN"


def test_bias_neutral_near_strike():
    assert detect_bias(100005.0, 100000.0, 50.0, 30.0, cfg()) == "NEUTRAL"


def test_bias_time_decay():
    assert detect_bias(100008.0, 100000.0, 31.0, 30.0, cfg()) == "NEUTRAL"
    assert detect_bias(100008.0, 100000.0, 10.0, 30.0, cfg()) == "UP"


def test_gate_blocks_early_trend():
    # clear trend (z=10), but 200s left > gate 90 → NEUTRAL (ignore early swing)
    assert detect_bias(100200.0, 100000.0, 20.0, 200.0, cfg()) == "NEUTRAL"


def test_gate_allows_late_trend():
    # same clear trend, 30s left <= gate 90 → fires
    assert detect_bias(100200.0, 100000.0, 20.0, 30.0, cfg()) == "UP"


def test_sigma_scales_with_time_left():
    buf = [(100000.0 + (i % 2) * 4.0, i * 0.5) for i in range(20)]
    s_late = sigma_remaining(buf, 25.0, cfg())
    s_early = sigma_remaining(buf, 100.0, cfg())
    assert s_early > s_late * 1.8


def test_sigma_ignores_steady_drift():
    buf = [(100000.0 + i * 3.0, i * 0.5) for i in range(20)]
    sig = sigma_remaining(buf, 30.0, cfg())
    assert sig < 20.0


def test_sigma_thin_buffer_uses_fallback():
    sig = sigma_remaining([(100000.0, 0.0)], 300.0, cfg())
    assert abs(sig - 30.0) < 1e-6
