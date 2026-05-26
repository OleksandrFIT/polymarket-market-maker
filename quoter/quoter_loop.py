"""Event-driven main quoter loop.

Connects: book updates → ladder recompute → executor sync.

* Maintains a ``dirty_markets`` set populated by book-update listeners
  and Binance price ticks.
* Every ``requote_min_interval_ms`` ms wakes up, processes dirty markets,
  and triggers ``executor.sync(market_id, desired_ladder)``.
* Per-market ``asyncio.Lock`` ensures atomic cancel-then-post (no race
  where two book updates trigger overlapping syncs on the same market).

This module is mode-agnostic: executor type (ShadowExecutor / PaperExecutor
/ LiveExecutor) is injected. Shadow + Paper modes don't generate real
fills; Live mode does.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Protocol

from quoter.book.book_manager import BookManager
from quoter.config import Config
from quoter.markets import Market
from quoter.ops.logger import get_logger
from quoter.risk.caps import RiskGuard
from quoter.strategy.inventory import Inventory
from quoter.strategy.ladder import Quote, compute_ladder

log = get_logger("quoter_loop")


class Executor(Protocol):
    def sync(self, market_id: str, desired: list[Quote]) -> dict[str, int]: ...
    def cancel_all_for_market(self, market_id: str) -> int: ...


class QuoterLoop:
    """Owns the quoter event loop.

    Lifecycle:
        loop = QuoterLoop(cfg, ...)
        loop.mark_dirty(market_id)      # called from listeners
        await loop.run()                # blocks; cancel task to stop
    """

    def __init__(
        self,
        cfg: Config,
        markets: list[Market],
        book_manager: BookManager,
        executor: Executor,
        inventory: Inventory,
        risk: RiskGuard,
        get_binance_price: Callable[[str], float | None],
    ) -> None:
        self.cfg = cfg
        self.markets = {m.market_id: m for m in markets}
        self.bm = book_manager
        self.exec = executor
        self.inv = inventory
        self.risk = risk
        self.get_binance = get_binance_price

        # Reverse map token_id → market_id
        self._token_to_market: dict[str, str] = {}
        for m in markets:
            self._token_to_market[m.yes_token] = m.market_id
            self._token_to_market[m.no_token] = m.market_id

        self._dirty: set[str] = set()
        self._market_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_requote_ts: dict[str, float] = {}
        self._tick_count = 0

    # ── Dirty-marking hooks (called from WS listeners) ──

    def mark_dirty_by_token(self, token_id: str) -> None:
        """Used as BookManager listener: token book changed → market dirty."""
        mid = self._token_to_market.get(token_id)
        if mid:
            self._dirty.add(mid)

    def mark_dirty_by_asset(self, asset: str) -> None:
        """Used by Binance ticker: BTC/ETH moved → all related markets dirty."""
        for m in self.markets.values():
            if m.asset == asset:
                self._dirty.add(m.market_id)

    # ── Main loop ──

    async def run(self) -> None:
        log.info("quoter_loop_started", markets=len(self.markets), mode=self.cfg.mode)
        try:
            while True:
                await asyncio.sleep(self.cfg.requote_min_interval_ms / 1000.0)
                self._tick_count += 1
                if not self._dirty:
                    continue
                self.risk.tick(time.time())
                if self.risk.stopped:
                    # Risk tripped: cancel everything and stop quoting
                    await self._panic_cancel_all()
                    log.error("quoter_loop_halted_risk", reason=self.risk.reason)
                    return
                pending = list(self._dirty)
                self._dirty.clear()
                for mid in pending:
                    await self._requote_market(mid)
        except asyncio.CancelledError:
            log.info("quoter_loop_cancelled")
            raise

    async def _requote_market(self, market_id: str) -> None:
        """Compute desired ladder and call executor.sync, under per-market lock."""
        market = self.markets.get(market_id)
        if market is None:
            return

        # Skip if market expired
        now = time.time()
        tte = market.expire_ts - now
        if tte <= self.cfg.requote_min_interval_ms / 1000.0:
            await self._on_market_expired(market_id)
            return

        # Throttle per-market
        last = self._last_requote_ts.get(market_id, 0.0)
        if (now - last) * 1000.0 < self.cfg.requote_min_interval_ms:
            # Re-mark dirty so next tick picks it up
            self._dirty.add(market_id)
            return

        async with self._market_locks[market_id]:
            self._last_requote_ts[market_id] = now
            yes_top = self.bm.top(market.yes_token)
            no_top = self.bm.top(market.no_token)
            mid_yes = self._infer_mid_yes(yes_top, no_top)
            if mid_yes is None:
                return

            committed = self._infer_committed(market, mid_yes)
            pos = self.inv.positions.get(market_id)
            yes_qty = pos.yes_qty if pos else 0
            no_qty = pos.no_qty if pos else 0

            desired = compute_ladder(
                self.cfg,
                mid_yes=mid_yes,
                time_to_expiry=tte,
                committed_side=committed,
                inventory_yes_qty=yes_qty,
                inventory_no_qty=no_qty,
            )
            self.exec.sync(market_id, desired)

    async def _on_market_expired(self, market_id: str) -> None:
        """Cancel all our quotes when a market window closes."""
        async with self._market_locks[market_id]:
            n = self.exec.cancel_all_for_market(market_id)
            if n > 0:
                log.info("market_expired_cancelled", market=market_id[:12], cancelled=n)

    async def _panic_cancel_all(self) -> None:
        """On risk trip: cancel everything across all markets."""
        for mid in list(self.markets.keys()):
            async with self._market_locks[mid]:
                self.exec.cancel_all_for_market(mid)

    # ── Helpers ──

    def _infer_mid_yes(self, yes_top, no_top) -> float | None:
        """Derive YES mid-price from the YES book primarily; fall back to
        NO book if YES is empty (mid_yes = 1 - mid_no)."""
        if yes_top is not None and yes_top.mid is not None:
            return yes_top.mid
        if no_top is not None and no_top.mid is not None:
            return 1.0 - no_top.mid
        return None

    def _infer_committed(self, market: Market, mid_yes: float) -> str | None:
        """Pick the side most likely to win at resolution.

        Phase 3: simple mid-based inference. Future: compare Binance spot
        to market strike (when strike discovery lands).
        """
        if mid_yes > 0.55:
            return "YES"
        if mid_yes < 0.45:
            return "NO"
        return None

    # ── Diagnostics ──

    def stats(self) -> dict:
        return {
            "tick_count": self._tick_count,
            "dirty_pending": len(self._dirty),
            "markets_tracked": len(self.markets),
        }
