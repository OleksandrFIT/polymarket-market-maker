"""Paper-mode executor: simulates fills via book traversal.

Same ``sync`` / ``cancel_all_for_market`` interface as ShadowExecutor, plus
``check_fills`` which is called by the QuoterLoop on each book update.

Fill model:
    For each LIVE bid we hold on side X at price P:
      if best_ask_X is not None AND P >= best_ask_X → SIMULATE FILL.
    The bid effectively "crossed" the ask, which in real CLOB means
    the seller's order would have hit our resting bid.

This is realistic for testing strategy: we only fill when the market
moves THROUGH our bid, not on initial placement (where ask is far above).

Partial fills are not modeled — a triggered quote fills fully at its bid
price. This is conservative for inventory but realistic for queue position
(if a seller dumps a big size, they take the whole quote anyway).
"""

from __future__ import annotations

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

    def __init__(self) -> None:
        # market_id → {(side, price) → PaperOrder}
        self._live: dict[str, dict[tuple[str, float], PaperOrder]] = {}
        # Track best-asks-at-time-of-sync per market for fill realism
        # market_id → {side → best_ask_at_last_sync}
        self._ask_at_sync: dict[str, dict[str, float | None]] = {}
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
        import time as _time
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

        Fill condition for a quote at price ``P`` on side ``S``:
          1. The current ``best_ask_S`` exists, AND
          2. ``P >= best_ask_S`` (our bid crosses the ask), AND
          3. ``best_ask_S < ask_at_placement_S`` (the ask has DROPPED since
             we placed — i.e. the seller's price-improvement actually moved
             through our bid; otherwise we'd have been the aggressor at
             placement, not a maker getting hit).
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
        out: list[FillEvent] = []
        for key in list(live.keys()):
            s, price = key
            if s != side or price < ask:
                continue
            order = live[key]
            # Require ask to have DROPPED since placement
            if order.ask_at_placement is not None and ask >= order.ask_at_placement:
                continue
            out.append(FillEvent(market_id, side, price, order.size))
            del live[key]
        return out

    def stats(self) -> dict[str, int]:
        return {
            "sync_count": self._sync_count,
            "cumulative_posts": self._cum_posts,
            "cumulative_cancels": self._cum_cancels,
            "cumulative_fills": self._cum_fills,
            "live_markets": len(self._live),
            "live_quotes_total": sum(len(s) for s in self._live.values()),
        }
