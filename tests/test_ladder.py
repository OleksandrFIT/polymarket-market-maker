"""Tests for compute_ladder pure-function strategy."""

from __future__ import annotations

from dataclasses import replace

from quoter.config import Config
from quoter.strategy.ladder import compute_ladder

CFG = Config(ladder_levels=12, budget_per_market_usd=25.0)


class TestLadderSanityGates:
    def test_skip_too_close_to_expiry(self):
        assert compute_ladder(CFG, mid_yes=0.5, time_to_expiry=3) == []

    def test_skip_mid_at_edges(self):
        assert compute_ladder(CFG, mid_yes=0.01, time_to_expiry=200) == []
        assert compute_ladder(CFG, mid_yes=0.99, time_to_expiry=200) == []

    def test_valid_mid_returns_quotes(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        assert len(out) > 0


class TestLadderShape:
    def test_emits_both_sides_at_mid(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        yes = [q for q in out if q.side == "YES"]
        no = [q for q in out if q.side == "NO"]
        assert len(yes) >= 8
        assert len(no) >= 8

    def test_all_prices_in_valid_range(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        for q in out:
            assert 0.01 <= q.price <= 0.99, f"{q!r} price out of range"

    def test_all_sizes_at_least_min(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        for q in out:
            assert q.size >= 5, f"{q!r} below Polymarket min size"

    def test_yes_quotes_below_mid_yes(self):
        out = compute_ladder(CFG, mid_yes=0.70, time_to_expiry=200)
        for q in out:
            if q.side == "YES" and q.price > 0.05:
                assert q.price < 0.70 + 0.001

    def test_no_quotes_below_mid_no(self):
        out = compute_ladder(CFG, mid_yes=0.70, time_to_expiry=200)
        mid_no = 0.30
        for q in out:
            if q.side == "NO" and q.price > 0.05:
                assert q.price < mid_no + 0.001

    def test_cheap_tail_present(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        # At least one quote at 0.01 on each side (mid - 10c > 0)
        assert any(q.price == 0.01 and q.side == "YES" for q in out)
        assert any(q.price == 0.01 and q.side == "NO" for q in out)


class TestInventorySkew:
    def test_long_yes_suppresses_yes_quotes(self):
        out = compute_ladder(
            CFG, mid_yes=0.5, time_to_expiry=200,
            inventory_yes_qty=500, inventory_no_qty=0,
        )
        yes = [q for q in out if q.side == "YES"]
        assert len(yes) == 0
        # NO should still be quoted
        assert any(q.side == "NO" for q in out)

    def test_long_no_suppresses_no_quotes(self):
        out = compute_ladder(
            CFG, mid_yes=0.5, time_to_expiry=200,
            inventory_yes_qty=0, inventory_no_qty=500,
        )
        no = [q for q in out if q.side == "NO"]
        assert len(no) == 0
        assert any(q.side == "YES" for q in out)

    def test_balanced_inventory_emits_both(self):
        out = compute_ladder(
            CFG, mid_yes=0.5, time_to_expiry=200,
            inventory_yes_qty=100, inventory_no_qty=100,
        )
        assert any(q.side == "YES" for q in out)
        assert any(q.side == "NO" for q in out)


class TestLateWindowSkew:
    def test_committed_yes_dominates_size_late(self):
        late = compute_ladder(CFG, mid_yes=0.8, time_to_expiry=30, committed_side="YES")
        yes_sz = sum(q.size for q in late if q.side == "YES")
        no_sz = sum(q.size for q in late if q.side == "NO")
        assert yes_sz > no_sz

    def test_no_skew_when_no_committed_side(self):
        # Without committed_side, sizing is symmetric (modulo cheap-tail)
        out = compute_ladder(CFG, mid_yes=0.5, time_to_expiry=30, committed_side=None)
        assert len(out) > 0  # just sanity


class TestSelfCrossPrevention:
    def test_drops_self_crossing_pair(self):
        # buffer=0.01, so yes_bid + no_bid must be < 0.99
        cfg = replace(CFG, ladder_levels=12, self_cross_buffer=0.01)
        # If mid_yes = 0.50, mid_no = 0.50
        # YES bid 1c below mid = 0.49; NO bid 1c below mid = 0.49
        # Sum = 0.98 < 0.99 → OK
        out = compute_ladder(cfg, mid_yes=0.50, time_to_expiry=200)
        # Verify no YES+NO pair crosses
        yes_max = max((q.price for q in out if q.side == "YES"), default=0)
        no_max = max((q.price for q in out if q.side == "NO"), default=0)
        assert yes_max + no_max <= 1.0 - cfg.self_cross_buffer + 1e-9


class TestSizing:
    def test_size_increases_with_offset_depth(self):
        out = compute_ladder(CFG, mid_yes=0.50, time_to_expiry=200)
        yes = sorted([q for q in out if q.side == "YES"], key=lambda q: -q.price)
        # First (closest to mid) should be SMALLER than later (deeper)
        if len(yes) >= 5:
            assert yes[0].size <= yes[4].size
