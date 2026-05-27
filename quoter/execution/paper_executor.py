"""Paper-mode executor: simulates fills via book traversal.

Same ``sync`` / ``cancel_all_for_market`` interface as ShadowExecutor, plus
``check_fills`` which is called by the QuoterLoop on each book update.

Two fill models are supported, selected by the ``realistic_mode`` flag:

LEGACY model (``realistic_mode=False``)
---------------------------------------
For each LIVE bid we hold on side X at price P:
    if best_ask_X is not None AND P >= best_ask_X
    AND best_ask_X dropped since placement  → SIMULATE FULL FILL.

This is what early tests rely on. It massively over-estimates fill rate
because it pretends we are the *only* maker at the price level.

REALISTIC model (``realistic_mode=True``)
-----------------------------------------
Real Polymarket order books carry 5-15 maker bids per price level. Tier-1
makers (Bonereaper) hold queue positions 1-3 thanks to months of priority
and ~5ms us-east-1 latency; a new entrant from a Slovakian laptop
(~110ms) joins at the BACK of the queue. When the ask drops to our price
the seller's taker order eats maker bids in time priority — most of the
size is absorbed before it ever reaches us.

We model this with three knobs:

1. Queue position
    Our seat in the FIFO at our price. Default 8 (mid-of-pack). Lower
    values (= better priority) fill more often. The queue ahead of us
    is assumed to be filled with maker bids of similar size to ours.

2. Probabilistic taker arrival
    When the ask crosses our bid we generate a random taker SELL of
    ``[taker_size_min, taker_size_max]`` shares. The first
    ``queue_position`` makers consume their size; we only fill from the
    LEFTOVER. If the taker size is small the leftover is zero → no fill.
    Otherwise we get a (possibly partial) fill capped at our quote size.

3. Latency penalty
    If the price moved within our ``latency_ms`` window between
    placement and the fill event we got beaten by faster makers and
    skip the event entirely. Concretely: if the gap between the ask at
    placement and the current ask is one tick (1 cent) and we were just
    posted, we lose the race.

Partial fills are supported in realistic mode — a quote that gets a
half-fill stays alive with reduced size for the next taker event.
"""

from __future__ import annotations

import random
import time as _time
from dataclasses import dataclass

from quoter.book.local_book import Top
from quoter.ops.logger import get_logger
from quoter.strategy.ladder import Quote

log = get_logger("paper_executor")


@dataclass
class PaperOrder:
    """One in-memory resting bid."""

    side: str
    price: float
    size: int
    placed_ts: float
    # Save the ask-at-placement so we can detect "crossed since placement"
    ask_at_placement: float | None = None


@dataclass
class FillEvent:
    """Simulated fill produced by ``check_fills``."""

    market_id: str
    side: str
    price: float
    size: int


class PaperExecutor:
    """In-memory order book + fill simulator. Implements the Executor protocol."""

    def __init__(
        self,
        *,
        realistic_mode: bool = False,
        queue_position: int = 8,
        taker_size_min: int = 5,
        taker_size_max: int = 200,
        latency_ms: int = 110,
        fill_prob_multiplier: float = 1.0,
        rng_seed: int | None = None,
    ) -> None:
        # market_id → {(side, price) → PaperOrder}
        self._live: dict[str, dict[tuple[str, float], PaperOrder]] = {}
        # Track best-asks-at-time-of-sync per market for fill realism
        # market_id → {side → best_ask_at_last_sync}
        self._ask_at_sync: dict[str, dict[str, float | None]] = {}
        # Realistic-mode knobs
        self._realistic = realistic_mode
        self._queue_pos = max(1, int(queue_position))
        self._taker_min = max(1, int(taker_size_min))
        self._taker_max = max(self._taker_min, int(taker_size_max))
        self._latency_sec = max(0.0, latency_ms / 1000.0)
        self._fill_mult = max(0.0, float(fill_prob_multiplier))
        self._rng = random.Random(rng_seed)
        # Metrics
        self._sync_count = 0
        self._cum_posts = 0
        self._cum_cancels = 0
        self._cum_fills = 0

    # ── Executor protocol ──

    def reset(self) -> None:
        """Drop all live orders and zero metrics — 'clear all data' action."""
        self._live.clear()
        self._ask_at_sync.clear()
        self._sync_count = 0
        self._cum_posts = 0
        self._cum_cancels = 0
        self._cum_fills = 0

    def for_market(self, market_id: str) -> dict[tuple[str, float], PaperOrder]:
        return self._live.setdefault(market_id, {})

    def sync(self, market_id: str, desired: list[Quote]) -> dict[str, int]:
        """Diff against in-memory live; post new, cancel stale."""
        self._sync_count += 1
        live = self.for_market(market_id)
        desired_map = {(q.side, q.price): q for q in desired}
        live_keys = set(live.keys())
        desired_keys = set(desired_map.keys())

        to_cancel = live_keys - desired_keys
        to_post = desired_keys - live_keys
        kept = live_keys & desired_keys

        now = _time.time()
        ask_snap = self._ask_at_sync.get(market_id, {})
        for key in to_cancel:
            del live[key]
        for key in to_post:
            q = desired_map[key]
            live[key] = PaperOrder(
                side=q.side,
                price=q.price,
                size=q.size,
                placed_ts=now,
                ask_at_placement=ask_snap.get(q.side),
            )

        self._cum_posts += len(to_post)
        self._cum_cancels += len(to_cancel)

        if to_post or to_cancel:
            log.info(
                "paper_sync",
                market=market_id[:12],
                posted=len(to_post),
                cancelled=len(to_cancel),
                kept=len(kept),
                live_total=len(live),
            )
        return {"posted": len(to_post), "cancelled": len(to_cancel), "kept": len(kept)}

    def cancel_all_for_market(self, market_id: str) -> int:
        live = self._live.pop(market_id, {})
        n = len(live)
        if n > 0:
            log.info("paper_cancel_all_for_market", market=market_id[:12], n=n)
            self._cum_cancels += n
        return n

    # ── Paper-specific: fill simulation ──

    def update_book_snapshot(
        self,
        market_id: str,
        yes_top: Top | None,
        no_top: Top | None,
    ) -> None:
        """Cache best-asks per side so that subsequent ``sync`` calls can
        attribute ``ask_at_placement`` correctly."""
        ask_snap = self._ask_at_sync.setdefault(market_id, {})
        if yes_top is not None:
            ask_snap["YES"] = yes_top.ask_px
        if no_top is not None:
            ask_snap["NO"] = no_top.ask_px

    def check_fills(
        self,
        market_id: str,
        yes_top: Top | None,
        no_top: Top | None,
    ) -> list[FillEvent]:
        """Return ``FillEvent``s for resting bids the ask has crossed THROUGH.

        Fill condition (legacy mode) for a quote at price ``P`` on side ``S``:
          1. The current ``best_ask_S`` exists, AND
          2. ``P >= best_ask_S`` (our bid crosses the ask), AND
          3. ``best_ask_S < ask_at_placement_S`` (the ask has DROPPED since
             we placed — i.e. the seller's price-improvement actually moved
             through our bid; otherwise we'd have been the aggressor at
             placement, not a maker getting hit).

        In realistic mode the same three gates apply, plus a latency check
        and a probabilistic queue/taker model — see module docstring.
        """
        live = self._live.get(market_id)
        if not live:
            return []
        fills: list[FillEvent] = []
        fills += self._check_side(market_id, live, "YES", yes_top)
        fills += self._check_side(market_id, live, "NO", no_top)
        self._cum_fills += len(fills)
        for f in fills:
            log.info(
                "paper_fill",
                market=market_id[:12],
                side=f.side, price=f.price, size=f.size,
            )
        return fills

    def _check_side(
        self,
        market_id: str,
        live: dict[tuple[str, float], PaperOrder],
        side: str,
        top: Top | None,
    ) -> list[FillEvent]:
        if top is None or top.ask_px is None:
            return []
        ask = top.ask_px
        now = _time.time()
        out: list[FillEvent] = []

        for key in list(live.keys()):
            s, price = key
            if s != side or price < ask:
                continue
            order = live[key]
            # Common gate: ask must have DROPPED since placement
            if order.ask_at_placement is not None and ask >= order.ask_at_placement:
                continue

            if not self._realistic:
                # Legacy model: full fill.
                out.append(FillEvent(market_id, side, price, order.size))
                del live[key]
                continue

            # ── Realistic model ──
            # Latency penalty: if we haven't been resting longer than our
            # network round-trip, faster makers got hit first.
            age = now - order.placed_ts
            if age < self._latency_sec:
                continue

            # Probabilistic taker arrival + queue absorption.
            taker_size = self._simulate_taker_arrival()
            fill_qty = self._fill_against_queue(taker_size, order.size)
            if fill_qty <= 0:
                continue

            out.append(FillEvent(market_id, side, price, fill_qty))
            if fill_qty >= order.size:
                del live[key]
            else:
                # Partial: shrink the resting order in place.
                order.size -= fill_qty

        return out

    # ── Realistic-mode helpers ──

    def _simulate_taker_arrival(self) -> int:
        """Draw a taker SELL size from the configured range.

        Polymarket takers come in a fat-tailed distribution — most are
        small price-discovery probes, occasionally a whale dumps. We
        approximate with a uniform draw over [min, max]; the
        ``fill_prob_multiplier`` scales the result so the operator can
        tune effective fill rate without recompiling.
        """
        base = self._rng.randint(self._taker_min, self._taker_max)
        scaled = int(round(base * self._fill_mult))
        return max(0, scaled)

    def _fill_against_queue(self, taker_size: int, our_size: int) -> int:
        """Compute how many shares the taker can give us.

        Each maker ahead of us is assumed to hold roughly 2× our quote
        size (Bonereaper-class makers run larger inventories than a
        retail entrant — empirically 20-50 shares per level vs. our 5-10).
        The taker eats them in FIFO order; whatever's left lands on us,
        capped at our size.
        """
        per_maker_ahead = max(1, our_size) * 2
        ahead = (self._queue_pos - 1) * per_maker_ahead
        leftover = taker_size - ahead
        if leftover <= 0:
            return 0
        return min(leftover, our_size)

    def stats(self) -> dict[str, int]:
        return {
            "sync_count": self._sync_count,
            "cumulative_posts": self._cum_posts,
            "cumulative_cancels": self._cum_cancels,
            "cumulative_fills": self._cum_fills,
            "live_markets": len(self._live),
            "live_quotes_total": sum(len(s) for s in self._live.values()),
        }
