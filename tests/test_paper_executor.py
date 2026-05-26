"""Tests for PaperExecutor — diff sync + fill simulation."""

from __future__ import annotations

import pytest

from quoter.book.local_book import Top
from quoter.execution.paper_executor import PaperExecutor
from quoter.strategy.ladder import Quote


def _quote(side: str, price: float, size: int = 10) -> Quote:
    return Quote(side, price, size)


class TestPaperSync:
    def test_initial_sync_posts_everything(self):
        ex = PaperExecutor()
        result = ex.sync("M1", [_quote("YES", 0.50), _quote("NO", 0.30)])
        assert result == {"posted": 2, "cancelled": 0, "kept": 0}
        assert len(ex.for_market("M1")) == 2

    def test_second_sync_same_desired_keeps_everything(self):
        ex = PaperExecutor()
        ex.sync("M1", [_quote("YES", 0.50), _quote("NO", 0.30)])
        result = ex.sync("M1", [_quote("YES", 0.50), _quote("NO", 0.30)])
        assert result == {"posted": 0, "cancelled": 0, "kept": 2}

    def test_sync_diff_partial_change(self):
        ex = PaperExecutor()
        ex.sync("M1", [_quote("YES", 0.50), _quote("NO", 0.30)])
        # Change YES from 0.50 to 0.49 (mid moved)
        result = ex.sync("M1", [_quote("YES", 0.49), _quote("NO", 0.30)])
        assert result == {"posted": 1, "cancelled": 1, "kept": 1}

    def test_cancel_all_for_market(self):
        ex = PaperExecutor()
        ex.sync("M1", [_quote("YES", 0.50)])
        ex.sync("M2", [_quote("NO", 0.30)])
        n = ex.cancel_all_for_market("M1")
        assert n == 1
        assert ex.for_market("M1") == {}
        # Other markets untouched
        assert len(ex.for_market("M2")) == 1

    def test_metrics_accumulate(self):
        ex = PaperExecutor()
        ex.sync("M1", [_quote("YES", 0.50), _quote("YES", 0.49)])
        ex.sync("M1", [_quote("YES", 0.49)])  # cancel 0.50
        stats = ex.stats()
        assert stats["cumulative_posts"] == 2
        assert stats["cumulative_cancels"] == 1
        assert stats["sync_count"] == 2


class TestPaperFills:
    def _make_top(self, bid: float | None, ask: float | None) -> Top:
        return Top(bid_px=bid, bid_sz=100.0, ask_px=ask, ask_sz=100.0)

    def test_no_fill_when_ask_above_our_bid(self):
        ex = PaperExecutor()
        # Place quote at 0.49 while ask is 0.51
        ex.update_book_snapshot("M1", yes_top=self._make_top(0.49, 0.51), no_top=None)
        ex.sync("M1", [_quote("YES", 0.49)])
        # Book unchanged: ask still 0.51
        fills = ex.check_fills("M1", yes_top=self._make_top(0.49, 0.51), no_top=None)
        assert fills == []
        assert len(ex.for_market("M1")) == 1

    def test_fill_when_ask_drops_to_our_bid(self):
        ex = PaperExecutor()
        ex.update_book_snapshot("M1", yes_top=self._make_top(0.49, 0.51), no_top=None)
        ex.sync("M1", [_quote("YES", 0.49, size=10)])
        # Ask drops from 0.51 → 0.49 → our bid crossed
        fills = ex.check_fills("M1", yes_top=self._make_top(0.48, 0.49), no_top=None)
        assert len(fills) == 1
        assert fills[0].side == "YES"
        assert fills[0].price == 0.49
        assert fills[0].size == 10
        # Quote removed from live
        assert ex.for_market("M1") == {}

    def test_no_double_fill_on_repeated_check(self):
        ex = PaperExecutor()
        ex.update_book_snapshot("M1", yes_top=self._make_top(0.49, 0.51), no_top=None)
        ex.sync("M1", [_quote("YES", 0.49)])
        ex.check_fills("M1", yes_top=self._make_top(0.48, 0.49), no_top=None)
        # Second check immediately after: quote already removed
        fills2 = ex.check_fills("M1", yes_top=self._make_top(0.48, 0.49), no_top=None)
        assert fills2 == []

    def test_only_quotes_above_or_at_ask_fill(self):
        ex = PaperExecutor()
        ex.update_book_snapshot("M1", yes_top=self._make_top(0.49, 0.55), no_top=None)
        ex.sync("M1", [
            _quote("YES", 0.50, size=5),
            _quote("YES", 0.45, size=7),
            _quote("YES", 0.40, size=9),
        ])
        # Ask drops to 0.45 → 0.50 and 0.45 fill, 0.40 doesn't
        fills = ex.check_fills("M1", yes_top=self._make_top(0.44, 0.45), no_top=None)
        filled_prices = sorted(f.price for f in fills)
        assert filled_prices == [0.45, 0.50]
        # 0.40 still alive
        assert ("YES", 0.40) in ex.for_market("M1")

    def test_skips_quote_placed_when_already_crossed(self):
        """If we placed at 0.49 when ask was already 0.49, that's a bad
        placement scenario; don't pretend it fills until ask DROPS BELOW
        the placement reference."""
        ex = PaperExecutor()
        # Ask snapshot at placement was 0.49 (already at our level)
        ex.update_book_snapshot("M1", yes_top=self._make_top(0.48, 0.49), no_top=None)
        ex.sync("M1", [_quote("YES", 0.49)])
        # Check with ask at 0.49 → should NOT fill (we'd have been an
        # aggressor at placement, not a maker fill)
        fills = ex.check_fills("M1", yes_top=self._make_top(0.48, 0.49), no_top=None)
        assert fills == []
        # But if ask drops below: fill
        fills2 = ex.check_fills("M1", yes_top=self._make_top(0.47, 0.48), no_top=None)
        assert len(fills2) == 1

    def test_no_fill_when_ask_none(self):
        ex = PaperExecutor()
        ex.sync("M1", [_quote("YES", 0.49)])
        fills = ex.check_fills("M1", yes_top=self._make_top(0.48, None), no_top=None)
        assert fills == []

    def test_both_sides_filled_independently(self):
        ex = PaperExecutor()
        ex.update_book_snapshot(
            "M1",
            yes_top=self._make_top(0.49, 0.51),
            no_top=self._make_top(0.30, 0.50),
        )
        ex.sync("M1", [_quote("YES", 0.50), _quote("NO", 0.35)])
        fills = ex.check_fills(
            "M1",
            yes_top=self._make_top(0.49, 0.50),   # YES ask drops to 0.50
            no_top=self._make_top(0.30, 0.34),    # NO ask drops to 0.34
        )
        sides = sorted(f.side for f in fills)
        assert sides == ["NO", "YES"]
