"""Shadow-mode executor: computes diff vs desired ladder, logs only.

No orders are placed. Used in MODE=shadow for sanity-checking the strategy
end-to-end before paper/live: WS → book → ladder → diff → STDOUT only.

The diff structure mirrors what ``OrderManager.sync`` will produce in paper
and live modes, so the logging format is forward-compatible.
"""

from __future__ import annotations

from dataclasses import dataclass

from quoter.ops.logger import get_logger
from quoter.strategy.ladder import Quote

log = get_logger("shadow_executor")


@dataclass
class ShadowFill:
    """Internal-only fake fill record (not used in shadow mode itself,
    but type-mirrored to paper executor for future symmetry)."""

    market_id: str
    side: str
    price: float
    size: int
    ts: float


class ShadowExecutor:
    """Tracks 'what we WOULD have posted' per market.

    Behavior:
        sync(market_id, desired) → compares desired vs in-memory live, logs
        diff. Updates internal state to match desired so subsequent calls
        only log incremental changes.

    No fills are simulated in shadow mode. For fill simulation use
    ``PaperExecutor`` (Phase 4).
    """

    def __init__(self) -> None:
        # market_id → set of (side, price) tuples that are "live" in shadow
        self._live: dict[str, set[tuple[str, float]]] = {}
        self._cumulative_posts: int = 0
        self._cumulative_cancels: int = 0
        self._sync_count: int = 0

    def for_market(self, market_id: str) -> set[tuple[str, float]]:
        return self._live.setdefault(market_id, set())

    def sync(self, market_id: str, desired: list[Quote]) -> dict[str, int]:
        """Compute diff vs in-memory live state. Returns counts.

        Returns:
            {"posted": N_new, "cancelled": N_removed, "kept": N_unchanged}
        """
        self._sync_count += 1
        current = self.for_market(market_id)
        desired_keys = {(q.side, q.price) for q in desired}

        to_cancel = current - desired_keys
        to_post = desired_keys - current
        kept = current & desired_keys

        # Update state
        self._live[market_id] = desired_keys
        self._cumulative_posts += len(to_post)
        self._cumulative_cancels += len(to_cancel)

        if to_post or to_cancel:
            log.info(
                "shadow_sync",
                market=market_id[:12],
                posted=len(to_post),
                cancelled=len(to_cancel),
                kept=len(kept),
                live_total=len(desired_keys),
            )
            # Log details only at DEBUG (configurable)
            if to_post:
                log.debug(
                    "shadow_post",
                    market=market_id[:12],
                    quotes=[(s, p) for s, p in sorted(to_post)],
                )
            if to_cancel:
                log.debug(
                    "shadow_cancel",
                    market=market_id[:12],
                    quotes=[(s, p) for s, p in sorted(to_cancel)],
                )

        return {"posted": len(to_post), "cancelled": len(to_cancel), "kept": len(kept)}

    def cancel_all_for_market(self, market_id: str) -> int:
        """Drop all 'live' quotes for one market (e.g. at expiry)."""
        n = len(self._live.get(market_id, ()))
        self._live.pop(market_id, None)
        if n > 0:
            log.info("shadow_cancel_all_for_market", market=market_id[:12], n=n)
            self._cumulative_cancels += n
        return n

    def stats(self) -> dict[str, int]:
        return {
            "sync_count": self._sync_count,
            "cumulative_posts": self._cumulative_posts,
            "cumulative_cancels": self._cumulative_cancels,
            "live_markets": len(self._live),
            "live_quotes_total": sum(len(s) for s in self._live.values()),
        }
