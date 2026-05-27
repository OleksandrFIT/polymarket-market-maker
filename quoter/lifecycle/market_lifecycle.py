"""Continuous market discovery + lifecycle management.

Re-discovers active 5m/15m windows every ``interval_sec`` and reconciles
the QuoterLoop's tracked-markets set with what's actually live on
Polymarket. Handles three transitions:

* **new market opened**: register in QuoterLoop, subscribe its tokens on
  the Polymarket WS, persist metadata.
* **market expired**: cancel all our quotes for it, mark resolved in
  SQLite (winner determined separately by ``resolution.py`` — Phase 5+).
* **token list changed**: rebuild PolyMarketWS subscription so we receive
  book events for the new tokens.

Runs as a long-lived asyncio task. Cancel via ``task.cancel()``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from quoter.config import Config
from quoter.feeds.poly_market_ws import PolyMarketWS
from quoter.lifecycle.resolution import fetch_resolution
from quoter.markets import Market, discover_markets
from quoter.ops.logger import get_logger
from quoter.persistence.state import State
from quoter.quoter_loop import QuoterLoop
from quoter.strategy.inventory import Inventory

log = get_logger("lifecycle")


class MarketLifecycle:
    """Periodic re-discovery + reconciliation with QuoterLoop."""

    def __init__(
        self,
        *,
        cfg: Config,
        quoter: QuoterLoop,
        executor: Any,
        poly_ws: PolyMarketWS,
        state: State | None,
        inventory: Inventory,
        on_market_added: Callable[[Market], Awaitable[None]] | None = None,
        interval_sec: int = 30,
    ) -> None:
        self.cfg = cfg
        self.quoter = quoter
        self.exec = executor
        self.poly_ws = poly_ws
        self.state = state
        self.inventory = inventory
        self.on_market_added = on_market_added
        self.interval_sec = interval_sec
        self._reconcile_count = 0
        # Expired markets we still hold a position in, awaiting Polymarket's
        # official resolution. Polled each reconcile until settled.
        self._pending: dict[str, Market] = {}

    async def run(self) -> None:
        log.info("market_lifecycle_started", interval=self.interval_sec)
        try:
            while True:
                try:
                    await self._reconcile()
                except Exception as e:
                    log.exception("lifecycle_iteration_failed", error=str(e))
                await asyncio.sleep(self.interval_sec)
        except asyncio.CancelledError:
            log.info("market_lifecycle_cancelled")
            raise

    async def _reconcile(self) -> None:
        """One reconciliation pass."""
        self._reconcile_count += 1
        now = time.time()
        # Remove markets that have already expired
        expired = [
            mid for mid, m in list(self.quoter.markets.items())
            if m.time_remaining(now) <= 0
        ]
        for mid in expired:
            await self._handle_expired(mid)

        # Discover what's live now
        live = await discover_markets(self.cfg)
        tracked_ids = set(self.quoter.markets.keys())

        added: list[Market] = []
        for m in live:
            if m.market_id not in tracked_ids:
                if self.quoter.add_market(m):
                    added.append(m)
                    log.info(
                        "market_added",
                        asset=m.asset, tf=m.timeframe,
                        condition=m.market_id[:12],
                        expires_in=int(m.time_remaining(now)),
                    )
                    if self.state is not None:
                        await self.state.upsert_market(
                            m.market_id, m.asset, m.timeframe,
                            m.open_ts, m.expire_ts,
                            m.yes_token, m.no_token,
                        )
                    if self.on_market_added is not None:
                        try:
                            await self.on_market_added(m)
                        except Exception as e:
                            log.warning("on_market_added_error", error=str(e))

        if added:
            # Re-subscribe WS with the updated token list. PolyMarketWS
            # closes the old socket; reconnect picks up the new list.
            self.poly_ws.set_subscriptions(self.quoter.known_tokens())
            log.info(
                "ws_resubscribed",
                token_count=len(self.quoter.known_tokens()),
                new_markets=len(added),
            )

        # Settle anything awaiting Polymarket's official resolution
        if self._pending:
            async with httpx.AsyncClient(
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (poly-quoter)"},
                follow_redirects=True,
            ) as client:
                await self._poll_resolutions(client)

    async def _handle_expired(self, market_id: str) -> None:
        """Stop quoting an expired market; hold its position for resolution."""
        m = self.quoter.remove_market(market_id)
        if m is None:
            return
        # Cancel any in-memory live quotes
        try:
            n = self.exec.cancel_all_for_market(market_id)
        except Exception as e:
            log.warning("expired_cancel_failed", market=market_id[:12], error=str(e))
            n = 0
        # If we hold a position, keep the market around to await resolution.
        # Positions are settled by ``_poll_resolutions`` once the CLOB reports
        # a winner — NOT dropped here (that would lose the realized P&L).
        if market_id in self.inventory.positions:
            self._pending[market_id] = m
        log.info(
            "market_expired",
            asset=m.asset, tf=m.timeframe,
            condition=market_id[:12], cancelled=n,
            awaiting_resolution=market_id in self._pending,
        )

    async def _poll_resolutions(self, client: Any) -> None:
        """Check each pending market for an official winner; settle if resolved."""
        for mid, m in list(self._pending.items()):
            side = await fetch_resolution(client, self.cfg.clob_host, m)
            if side is None:
                continue  # not resolved yet — retry next pass
            pnl = self.inventory.on_resolve(mid, side)
            if self.state is not None:
                await self.state.mark_market_resolved(mid, side, realized_pnl=pnl)
            del self._pending[mid]
            log.info(
                "market_resolved",
                asset=m.asset, tf=m.timeframe,
                condition=mid[:12], winning_side=side,
                realized_pnl=round(self.inventory.realized_pnl, 2),
            )
