"""Competitor edge analysis — pure reconstruction + aggregation (no I/O)."""

from quoter.analysis.competitor import (
    Trade, WindowResult, aggregate, reconstruct_window,
)


def test_balanced_hedge_up_wins():
    # Up 5@0.45 + Down 5@0.50 = pair 0.95; Up wins. Hedge edge only, no naked.
    r = reconstruct_window("w1",
        [Trade("Up", 5, 0.45), Trade("Down", 5, 0.50)], "Up")
    assert r.matched == 5
    assert abs(r.pair_cost - 0.95) < 1e-9
    assert abs(r.pair_pnl - 0.25) < 1e-9        # 5 * (1 - 0.95)
    assert r.naked_shares == 0
    assert r.naked_pnl == 0.0
    assert abs(r.net - 0.25) < 1e-9


def test_balanced_hedge_is_direction_independent():
    # Same trades, Down wins → same pair_pnl (hedge doesn't care who wins).
    r = reconstruct_window("w2",
        [Trade("Up", 5, 0.45), Trade("Down", 5, 0.50)], "Down")
    assert abs(r.pair_pnl - 0.25) < 1e-9
    assert r.naked_pnl == 0.0


def test_imbalanced_naked_side_wins():
    # Up 10@0.30 + Down 5@0.40; Up wins. matched 5, naked 5 Up @0.30 wins.
    r = reconstruct_window("w3",
        [Trade("Up", 10, 0.30), Trade("Down", 5, 0.40)], "Up")
    assert abs(r.pair_cost - 0.70) < 1e-9
    assert abs(r.pair_pnl - 1.50) < 1e-9        # 5 * (1 - 0.70)
    assert r.naked_shares == 5 and r.naked_side == "Up"
    assert abs(r.naked_pnl - 3.50) < 1e-9       # 5 * (1 - 0.30)
    assert abs(r.net - 5.00) < 1e-9


def test_imbalanced_naked_side_loses():
    # Same trades, Down wins → naked Up loses.
    r = reconstruct_window("w4",
        [Trade("Up", 10, 0.30), Trade("Down", 5, 0.40)], "Down")
    assert abs(r.pair_pnl - 1.50) < 1e-9        # hedge still pays
    assert abs(r.naked_pnl - (-1.50)) < 1e-9    # 5 * (0 - 0.30)
    assert abs(r.net - 0.00) < 1e-9


def test_pure_one_sided_window_loses():
    # Only Up 5@0.40, Down resolves winner → fully naked loss, no pair.
    r = reconstruct_window("w5", [Trade("Up", 5, 0.40)], "Down")
    assert r.matched == 0 and r.pair_pnl == 0.0
    assert r.naked_shares == 5 and r.naked_side == "Up"
    assert abs(r.naked_pnl - (-2.00)) < 1e-9    # 5 * (0 - 0.40)
    assert abs(r.net - (-2.00)) < 1e-9
