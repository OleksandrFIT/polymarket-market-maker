"""Tests for the realistic fill model in PaperExecutor.

The naive (legacy) model fills 100% on every ask-cross. The realistic
model layers on queue priority, probabilistic taker arrivals and a
latency penalty so paper-mode P&L tracks live conditions where
new entrants only catch 5-30% of crosses.
"""

from __future__ import annotations

import time as _time

from quoter.book.local_book import Top
from quoter.execution.paper_executor import PaperExecutor
from quoter.strategy.ladder import Quote


def _quote(side: str, price: float, size: int = 10) -> Quote:
    return Quote(side, price, size)


def _top(bid: float | None, ask: float | None) -> Top:
    return Top(bid_px=bid, bid_sz=100.0, ask_px=ask, ask_sz=100.0)


def _make_executor(
    *,
    realistic: bool,
    queue_position: int = 8,
    taker_size_min: int = 5,
    taker_size_max: int = 200,
    latency_ms: int = 0,
    fill_prob_multiplier: float = 1.0,
    seed: int = 42,
) -> PaperExecutor:
    return PaperExecutor(
        realistic_mode=realistic,
        queue_position=queue_position,
        taker_size_min=taker_size_min,
        taker_size_max=taker_size_max,
        latency_ms=latency_ms,
        fill_prob_multiplier=fill_prob_multiplier,
        rng_seed=seed,
    )


def _place_and_age(
    ex: PaperExecutor,
    market: str,
    side: str,
    price: float,
    ask_at_placement: float,
    size: int = 10,
    age_sec: float = 1.0,
) -> None:
    """Place one quote then back-date its ``placed_ts`` so latency gates pass."""
    ex.update_book_snapshot(
        market,
        yes_top=_top(price - 0.01, ask_at_placement) if side == "YES" else None,
        no_top=_top(price - 0.01, ask_at_placement) if side == "NO" else None,
    )
    ex.sync(market, [_quote(side, price, size=size)])
    order = ex.for_market(market)[(side, price)]
    order.placed_ts = _time.time() - age_sec


class TestLegacyBehaviorPreserved:
    """With realistic_mode OFF we must match the original 100%-fill model."""

    def test_full_fill_on_cross(self):
        ex = _make_executor(realistic=False)
        ex.update_book_snapshot("M1", yes_top=_top(0.49, 0.51), no_top=None)
        ex.sync("M1", [_quote("YES", 0.49, size=10)])
        fills = ex.check_fills("M1", yes_top=_top(0.48, 0.49), no_top=None)
        assert len(fills) == 1
        assert fills[0].size == 10  # full fill
        assert ex.for_market("M1") == {}

    def test_no_fill_without_ask_drop(self):
        ex = _make_executor(realistic=False)
        ex.update_book_snapshot("M1", yes_top=_top(0.49, 0.49), no_top=None)
        ex.sync("M1", [_quote("YES", 0.49, size=10)])
        # Ask never drops below ask_at_placement
        fills = ex.check_fills("M1", yes_top=_top(0.49, 0.49), no_top=None)
        assert fills == []


class TestRealisticFillRate:
    """Aggregate fill rate over many simulated taker events should land
    in the 10-30% band the design brief calls out."""

    def _simulate_events(
        self,
        ex: PaperExecutor,
        n_events: int = 500,
        size_per_quote: int = 10,
    ) -> tuple[int, int]:
        """Run N taker arrivals. Returns (filled_shares, max_possible)."""
        filled = 0
        for i in range(n_events):
            mkt = f"M{i}"
            _place_and_age(
                ex, mkt, "YES", 0.49,
                ask_at_placement=0.51,
                size=size_per_quote,
                age_sec=5.0,  # well past latency gate
            )
            fills = ex.check_fills(mkt, yes_top=_top(0.48, 0.49), no_top=None)
            filled += sum(f.size for f in fills)
        return filled, n_events * size_per_quote

    def test_average_fill_rate_in_band(self):
        ex = _make_executor(
            realistic=True,
            queue_position=8,
            taker_size_min=5,
            taker_size_max=200,
            latency_ms=0,
            seed=12345,
        )
        filled, possible = self._simulate_events(ex, n_events=1000)
        rate = filled / possible
        # Brief: 5-30% with default knobs. Use a slightly wider band so the
        # test doesn't flake on RNG distribution edges.
        assert 0.05 <= rate <= 0.35, f"fill rate {rate:.2%} out of band"

    def test_legacy_mode_fills_everything(self):
        """Same scenario, legacy mode → ~100%."""
        ex = _make_executor(realistic=False, seed=12345)
        filled, possible = self._simulate_events(ex, n_events=200)
        assert filled == possible


class TestLatencyPenalty:
    """If the quote hasn't been resting longer than our network RTT we
    couldn't possibly have raced the faster makers — skip the event."""

    def test_just_placed_quote_does_not_fill(self):
        ex = _make_executor(
            realistic=True, latency_ms=110, taker_size_min=10_000,
            taker_size_max=10_000, queue_position=1,
        )
        ex.update_book_snapshot("M1", yes_top=_top(0.49, 0.51), no_top=None)
        ex.sync("M1", [_quote("YES", 0.49, size=10)])
        # Quote was just placed (age ~0ms) < 110ms latency → no fill even
        # though the taker size is enormous and we're queue position 1.
        fills = ex.check_fills("M1", yes_top=_top(0.48, 0.49), no_top=None)
        assert fills == []

    def test_aged_quote_can_fill(self):
        ex = _make_executor(
            realistic=True, latency_ms=110, taker_size_min=10_000,
            taker_size_max=10_000, queue_position=1, seed=1,
        )
        _place_and_age(
            ex, "M1", "YES", 0.49, ask_at_placement=0.51,
            size=10, age_sec=1.0,
        )
        fills = ex.check_fills("M1", yes_top=_top(0.48, 0.49), no_top=None)
        assert len(fills) == 1
        assert fills[0].size == 10  # huge taker, position 1 → full fill


class TestQueuePosition:
    """Better queue priority → higher fill rate."""

    def _measure_rate(self, queue_position: int, seed: int = 7) -> float:
        ex = _make_executor(
            realistic=True,
            queue_position=queue_position,
            taker_size_min=5,
            taker_size_max=200,
            latency_ms=0,
            seed=seed,
        )
        filled = 0
        possible = 0
        for i in range(500):
            mkt = f"M{i}"
            _place_and_age(
                ex, mkt, "YES", 0.49,
                ask_at_placement=0.51, size=10, age_sec=5.0,
            )
            fills = ex.check_fills(mkt, yes_top=_top(0.48, 0.49), no_top=None)
            filled += sum(f.size for f in fills)
            possible += 10
        return filled / possible

    def test_front_of_queue_beats_back(self):
        rate_front = self._measure_rate(queue_position=3)
        rate_back = self._measure_rate(queue_position=12)
        assert rate_front > rate_back, (
            f"position 3 ({rate_front:.2%}) should beat position 12 ({rate_back:.2%})"
        )

    def test_position_one_with_big_takers_fills_often(self):
        """Sanity floor: even the worst-case realistic config should not
        produce zero fills when conditions are favourable."""
        rate = self._measure_rate(queue_position=1)
        assert rate > 0.10


class TestPartialFills:
    """Realistic mode should permit partial fills; the residue keeps
    resting at reduced size."""

    def test_partial_fill_shrinks_resting_size(self):
        # queue_position=1 + taker_size=15 + our_size=20 → fill 15, leave 5
        ex = _make_executor(
            realistic=True, queue_position=1, taker_size_min=15,
            taker_size_max=15, latency_ms=0, seed=99,
        )
        _place_and_age(
            ex, "M1", "YES", 0.49, ask_at_placement=0.51,
            size=20, age_sec=1.0,
        )
        fills = ex.check_fills("M1", yes_top=_top(0.48, 0.49), no_top=None)
        assert len(fills) == 1
        assert fills[0].size == 15
        residue = ex.for_market("M1").get(("YES", 0.49))
        assert residue is not None
        assert residue.size == 5


class TestAskDropGatePreserved:
    """The 'ask must have dropped since placement' guard applies to
    realistic mode too — otherwise we'd have been the aggressor."""

    def test_realistic_skips_when_ask_not_dropped(self):
        ex = _make_executor(
            realistic=True, queue_position=1, taker_size_min=1000,
            taker_size_max=1000, latency_ms=0,
        )
        # Placed when ask was already at 0.49 (we'd be a taker)
        ex.update_book_snapshot("M1", yes_top=_top(0.48, 0.49), no_top=None)
        ex.sync("M1", [_quote("YES", 0.49, size=10)])
        fills = ex.check_fills("M1", yes_top=_top(0.48, 0.49), no_top=None)
        assert fills == []
