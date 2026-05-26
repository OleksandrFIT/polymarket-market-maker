"""Tests for LocalBook."""

from __future__ import annotations

from quoter.book.local_book import LocalBook


def _bid(p: str, s: str) -> dict:
    return {"price": p, "size": s}


class TestLocalBookSnapshot:
    def test_empty_book_has_no_top(self):
        b = LocalBook()
        assert b.top() is None
        assert b.is_empty()

    def test_apply_full_populates_book(self):
        b = LocalBook()
        b.apply_full(
            bids=[_bid("0.60", "100"), _bid("0.61", "50"), _bid("0.62", "20")],
            asks=[_bid("0.65", "80"), _bid("0.66", "40")],
        )
        top = b.top()
        assert top is not None
        # bids sorted ascending in input → best is highest = 0.62
        assert top.bid_px == 0.62
        assert top.bid_sz == 20.0
        # asks sorted ascending → best is lowest = 0.65
        assert top.ask_px == 0.65
        assert top.ask_sz == 80.0
        # mid and spread
        assert abs(top.mid - 0.635) < 1e-9
        assert abs(top.spread - 0.03) < 1e-9

    def test_apply_full_drops_zero_size(self):
        b = LocalBook()
        b.apply_full(
            bids=[_bid("0.60", "0"), _bid("0.61", "50")],
            asks=[_bid("0.65", "0")],
        )
        top = b.top()
        assert top is not None
        assert top.bid_px == 0.61
        assert top.ask_px is None  # no asks

    def test_apply_full_replaces_previous(self):
        b = LocalBook()
        b.apply_full(bids=[_bid("0.60", "100")], asks=[])
        b.apply_full(bids=[_bid("0.70", "200")], asks=[_bid("0.80", "10")])
        top = b.top()
        assert top.bid_px == 0.70
        assert top.ask_px == 0.80

    def test_apply_full_handles_malformed_levels(self):
        b = LocalBook()
        b.apply_full(
            bids=[{"price": "0.60", "size": "100"}, {"price": "BAD", "size": "5"}, {}],
            asks=[_bid("0.65", "80")],
        )
        top = b.top()
        assert top.bid_px == 0.60
        assert top.ask_px == 0.65


class TestLocalBookDeltas:
    def setup_method(self):
        self.b = LocalBook()
        self.b.apply_full(
            bids=[_bid("0.60", "100"), _bid("0.61", "50")],
            asks=[_bid("0.65", "80"), _bid("0.66", "40")],
        )

    def test_delta_add_new_level(self):
        self.b.apply_delta({"price_changes": [
            {"price": "0.62", "size": "30", "side": "BUY"},
        ]})
        top = self.b.top()
        assert top.bid_px == 0.62
        assert top.bid_sz == 30.0

    def test_delta_update_size(self):
        self.b.apply_delta({"price_changes": [
            {"price": "0.61", "size": "999", "side": "BUY"},
        ]})
        top = self.b.top()
        assert top.bid_px == 0.61
        assert top.bid_sz == 999.0

    def test_delta_remove_with_zero_size(self):
        self.b.apply_delta({"price_changes": [
            {"price": "0.61", "size": "0", "side": "BUY"},
        ]})
        top = self.b.top()
        assert top.bid_px == 0.60  # 0.61 gone, next best is 0.60

    def test_delta_with_changes_field_alias(self):
        # Some Polymarket events use 'changes' instead of 'price_changes'
        self.b.apply_delta({"changes": [
            {"price": "0.65", "size": "0", "side": "SELL"},
        ]})
        top = self.b.top()
        assert top.ask_px == 0.66  # 0.65 removed, next is 0.66


class TestLocalBookDepth:
    def test_depth_returns_top_n_levels_in_order(self):
        b = LocalBook()
        b.apply_full(
            bids=[_bid(f"0.{50+i}", "10") for i in range(10)],
            asks=[_bid(f"0.{60+i}", "20") for i in range(10)],
        )
        bid_depth = b.depth("BUY", n=3)
        # Best bid first (0.59), then 0.58, 0.57
        assert [p for p, _ in bid_depth] == [0.59, 0.58, 0.57]
        ask_depth = b.depth("SELL", n=3)
        assert [p for p, _ in ask_depth] == [0.60, 0.61, 0.62]

    def test_depth_respects_n_smaller_than_levels(self):
        b = LocalBook()
        b.apply_full(bids=[_bid("0.5", "10"), _bid("0.6", "20")], asks=[])
        assert b.depth("BUY", n=1) == [(0.6, 20.0)]


class TestLocalBookTimestamps:
    def test_apply_full_updates_ts(self):
        b = LocalBook()
        b.apply_full(bids=[_bid("0.5", "1")], asks=[], ts=1234.5)
        assert b.last_update_ts == 1234.5

    def test_apply_full_zero_ts_does_not_overwrite(self):
        b = LocalBook()
        b.apply_full(bids=[_bid("0.5", "1")], asks=[], ts=100.0)
        b.apply_full(bids=[_bid("0.6", "1")], asks=[], ts=0.0)
        assert b.last_update_ts == 100.0
