"""Tests for Inventory + Position cost-basis accounting."""

from __future__ import annotations

import pytest

from quoter.strategy.inventory import Inventory


class TestPositionFills:
    def test_fill_yes_adds_qty_and_cost(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", price=0.40, qty=10)
        p = inv.positions["M1"]
        assert p.yes_qty == 10
        assert p.yes_cost_total == 4.0
        assert p.yes_avg == 0.40
        assert p.no_qty == 0

    def test_average_cost_with_multiple_fills(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", price=0.40, qty=10)
        inv.on_fill("M1", "YES", price=0.50, qty=10)
        p = inv.positions["M1"]
        assert p.yes_qty == 20
        assert p.yes_avg == pytest.approx(0.45)

    def test_zero_or_negative_qty_ignored(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", price=0.40, qty=0)
        inv.on_fill("M1", "YES", price=0.40, qty=-5)
        assert "M1" not in inv.positions or inv.positions["M1"].yes_qty == 0

    def test_both_sides_independent(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", price=0.40, qty=10)
        inv.on_fill("M1", "NO", price=0.55, qty=8)
        p = inv.positions["M1"]
        assert p.yes_qty == 10 and p.no_qty == 8
        assert p.matched == 8
        assert p.net_yes_minus_no == 2

    def test_n_fills_counter(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", 0.4, 10)
        inv.on_fill("M2", "NO", 0.5, 5)
        assert inv.n_fills == 2


class TestMerge:
    def test_merge_matched_pairs_yields_realized(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", 0.40, 100)
        inv.on_fill("M1", "NO", 0.55, 100)
        # cost basis: 100*0.40 + 100*0.55 = $95
        # merge 100 pairs → $100 revenue → +$5
        inv.on_merge("M1", 100)
        assert inv.realized_pnl == pytest.approx(5.0)
        assert inv.positions["M1"].yes_qty == 0
        assert inv.positions["M1"].no_qty == 0

    def test_merge_caps_at_matched_count(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", 0.40, 50)
        inv.on_fill("M1", "NO", 0.55, 100)
        # Only 50 pairs available; merge(200) should cap at 50
        inv.on_merge("M1", 200)
        assert inv.positions["M1"].yes_qty == 0
        assert inv.positions["M1"].no_qty == 50  # 50 unmatched NO left

    def test_merge_on_unknown_market_noop(self):
        inv = Inventory()
        inv.on_merge("BOGUS", 100)  # should not raise
        assert inv.realized_pnl == 0

    def test_merge_when_overround_loses_money(self):
        # If avg(YES) + avg(NO) > 1, merging is a net loss
        inv = Inventory()
        inv.on_fill("M1", "YES", 0.60, 10)
        inv.on_fill("M1", "NO", 0.50, 10)
        # cost: 6 + 5 = 11; merge 10 pairs = $10 → -$1
        inv.on_merge("M1", 10)
        assert inv.realized_pnl == pytest.approx(-1.0)


class TestResolution:
    def test_resolution_winning_side(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", 0.30, 100)
        inv.on_fill("M1", "NO", 0.65, 50)
        # cost: 30 + 32.5 = $62.5
        # If YES wins: 100 shares pay $1 each = $100 → +$37.5
        inv.on_resolve("M1", "YES")
        assert inv.realized_pnl == pytest.approx(37.5)
        assert "M1" not in inv.positions

    def test_resolution_losing_side(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", 0.30, 100)
        inv.on_fill("M1", "NO", 0.65, 50)
        # If NO wins: 50 shares pay $1 = $50; total cost $62.5 → -$12.5
        inv.on_resolve("M1", "NO")
        assert inv.realized_pnl == pytest.approx(-12.5)

    def test_resolve_unknown_market_noop(self):
        inv = Inventory()
        inv.on_resolve("BOGUS", "YES")
        assert inv.realized_pnl == 0

    def test_on_resolve_returns_realized_delta(self):
        inv = Inventory()
        inv.on_fill("M1", "NO", 0.40, 100)  # cost $40, NO wins → $100
        delta = inv.on_resolve("M1", "NO")
        assert delta == pytest.approx(60.0)

    def test_on_resolve_unknown_market_returns_zero(self):
        inv = Inventory()
        assert inv.on_resolve("BOGUS", "YES") == 0.0


class TestSnapshot:
    def test_snapshot_includes_realized_and_open(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", 0.4, 10)
        inv.on_fill("M2", "NO", 0.5, 20)
        snap = inv.snapshot()
        assert snap["realized_pnl"] == 0.0
        assert snap["open_markets"] == 2
        assert "M1" in snap["positions"]
        assert snap["positions"]["M1"]["yes_qty"] == 10
        assert snap["n_fills"] == 2

    def test_snapshot_after_resolution(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", 0.4, 100)
        inv.on_resolve("M1", "YES")
        snap = inv.snapshot()
        assert snap["open_markets"] == 0
        assert snap["realized_pnl"] == pytest.approx(60.0)


class TestReset:
    def test_reset_clears_all_state(self):
        inv = Inventory()
        inv.on_fill("M1", "YES", 0.40, 10)
        inv.on_fill("M2", "NO", 0.50, 20)
        inv.on_resolve("M2", "NO")
        inv.reset()
        assert inv.realized_pnl == 0.0
        assert inv.n_fills == 0
        assert inv.n_merges == 0
        assert inv.n_resolutions == 0
        assert len(inv.positions) == 0
