"""Crypto taker fee per share = 0.07 * min(price, 1-price) (crypto_fees_v2, verified 2026-07-11):
peaks at 0.50, ~0 at extremes. Maker rebate = 0.2 * taker fee (per maker fill)."""
from quoter.runner.top_book_planner import taker_fee, maker_rebate


def test_peak_at_half():
    assert abs(taker_fee(0.50) - 0.035) < 1e-9        # 0.07 * 0.5


def test_cheap_at_extremes():
    assert taker_fee(0.95) < taker_fee(0.50)
    assert abs(taker_fee(0.95) - 0.07 * 0.05) < 1e-9
    assert abs(taker_fee(0.05) - 0.07 * 0.05) < 1e-9


def test_symmetric():
    assert abs(taker_fee(0.30) - taker_fee(0.70)) < 1e-9


def test_bounds_clamped():
    assert taker_fee(0.0) == 0.0 and taker_fee(1.0) == 0.0


def test_maker_rebate_is_fifth_of_taker_fee():
    assert abs(maker_rebate(0.50) - 0.2 * 0.035) < 1e-9   # 0.2 * taker_fee(0.50) = 0.007/share
    assert abs(maker_rebate(0.10) - 0.2 * taker_fee(0.10)) < 1e-9
    assert maker_rebate(0.50) > maker_rebate(0.90)        # bigger near mid
