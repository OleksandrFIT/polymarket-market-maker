"""Crypto taker fee per share = 1.80% * min(price, 1-price): peaks at 0.50, ~0 at extremes."""
from quoter.runner.top_book_planner import taker_fee


def test_peak_at_half():
    assert abs(taker_fee(0.50) - 0.009) < 1e-9        # 0.018 * 0.5


def test_cheap_at_extremes():
    assert taker_fee(0.95) < taker_fee(0.50)
    assert abs(taker_fee(0.95) - 0.018 * 0.05) < 1e-9
    assert abs(taker_fee(0.05) - 0.018 * 0.05) < 1e-9


def test_symmetric():
    assert abs(taker_fee(0.30) - taker_fee(0.70)) < 1e-9


def test_bounds_clamped():
    assert taker_fee(0.0) == 0.0 and taker_fee(1.0) == 0.0
