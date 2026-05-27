"""LIVE-mode executor — real CLOB orders.

Implements the same ``Executor`` protocol as ShadowExecutor / PaperExecutor
so ``QuoterLoop`` can drive it identically. Additionally exposes:

* ``instant_repost(market_id, side, price, size)`` — fast re-post of the
  same price/size after a fill, triggered from user WS events. This is
  the FILL-EVENT-DRIVEN path (Bonereaper-style).
* ``on_user_event(msg)`` — handler for incoming user WS messages: trade
  (fill), order (status change).
* ``cancel_all_open()`` — startup recovery; wipes orphan orders.

Mode of operation:
  1. ``sync(market_id, desired)`` — called by QuoterLoop on book events:
     diff vs current live orders, cancel stale, post new (as maker via
     ``post_only=True``).
  2. ``on_user_event(trade)`` → ``inventory.on_fill(...)`` +
     ``instant_repost(...)`` triggers IMMEDIATE refresh of that level.
  3. Internal map ``self.live: {order_id: LiveOrder}`` is the source of
     truth for "what's currently in the book under our control".
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from quoter.execution.clob_client import ClobOps
from quoter.ops.logger import get_logger
from quoter.strategy.ladder import Quote

log = get_logger("live_executor")

FillCallback = Callable[[str, str, float, int], Awaitable[None]]
"""``async def on_fill(market_id, side, price, qty)`` — fires after each real fill."""


@dataclass
class LiveOrder:
    """One real order placed via CLOB. Tracked by its order_id."""
    order_id: str
    market_id: str
    token_id: str
    side: str  # "YES" | "NO"
    price: float
    size: int
    placed_at: float


class LiveExecutor:
    """Real Polymarket CLOB executor with fill-event-driven instant repost."""

    def __init__(
        self,
        clob: ClobOps,
        token_to_market_side: dict[str, tuple[str, str]] | None = None,
        on_fill: FillCallback | None = None,
    ) -> None:
        self.clob = clob
        # Lookup: token_id → (market_id, "YES"|"NO") so fill events can be
        # routed to the right market.
        self._token_to_market_side = token_to_market_side or {}
        self._on_fill = on_fill

        self.live: dict[str, LiveOrder] = {}  # order_id → LiveOrder
        # Per-market locks for atomic sync (mirror PaperExecutor / QuoterLoop)
        self._market_locks: dict[str, asyncio.Lock] = {}

        # Metrics
        self._sync_count = 0
        self._cum_posts = 0
        self._cum_cancels = 0
        self._cum_fills = 0
        self._cum_instant_reposts = 0
        self._failed_posts = 0

    # ── Registration (called by main on market discovery) ──

    def register_token(self, token_id: str, market_id: str, side: str) -> None:
        self._token_to_market_side[token_id] = (market_id, side)

    def set_fill_callback(self, cb: FillCallback) -> None:
        self._on_fill = cb

    def _lock(self, market_id: str) -> asyncio.Lock:
        lock = self._market_locks.get(market_id)
        if lock is None:
            lock = asyncio.Lock()
            self._market_locks[market_id] = lock
        return lock

    # ── Executor protocol ──

    def for_market(self, market_id: str) -> dict[tuple[str, float], LiveOrder]:
        """Return live orders for one market as {(side, price): order}."""
        return {
            (o.side, o.price): o
            for o in self.live.values()
            if o.market_id == market_id
        }

    def sync(self, market_id: str, desired: list[Quote]) -> dict[str, int]:
        """Sync interface — but actual work is async. Returns counts ESTIMATE."""
        # The Executor protocol is sync; we have to schedule async work.
        # In LiveExecutor we just queue the sync — actual posting is async.
        live = self.for_market(market_id)
        desired_keys = {(q.side, q.price) for q in desired}
        current_keys = set(live.keys())

        to_cancel_ids = [live[k].order_id for k in (current_keys - desired_keys)]
        to_post = [q for q in desired if (q.side, q.price) not in current_keys]
        kept = len(current_keys & desired_keys)

        self._sync_count += 1

        # Fire-and-forget async sync (don't block quoter loop)
        if to_cancel_ids or to_post:
            asyncio.create_task(
                self._async_sync(market_id, to_cancel_ids, to_post),
                name=f"live_sync_{market_id[:10]}",
            )

        return {
            "posted": len(to_post),    # ESTIMATE (may fail)
            "cancelled": len(to_cancel_ids),
            "kept": kept,
        }

    async def _async_sync(
        self,
        market_id: str,
        cancel_ids: list[str],
        to_post: list[Quote],
    ) -> None:
        """Actually execute cancels + posts under per-market lock."""
        async with self._lock(market_id):
            # 1. Cancel stale
            if cancel_ids:
                n = await self.clob.cancel_orders(cancel_ids)
                self._cum_cancels += n
                for oid in cancel_ids:
                    self.live.pop(oid, None)

            # 2. Post new (skip if we don't know the token mapping for this side)
            for q in to_post:
                token_id = self._token_for(market_id, q.side)
                if not token_id:
                    log.warning("no_token_for_side", market=market_id[:10], side=q.side)
                    continue
                resp = await self.clob.place_limit(
                    token_id=token_id, price=q.price, size=q.size,
                    side="BUY", post_only=True,
                )
                if resp is None:
                    self._failed_posts += 1
                    continue
                oid = resp["order_id"]
                if not oid:
                    self._failed_posts += 1
                    continue
                self.live[oid] = LiveOrder(
                    order_id=oid, market_id=market_id, token_id=token_id,
                    side=q.side, price=q.price, size=q.size, placed_at=time.time(),
                )
                self._cum_posts += 1

    def cancel_all_for_market(self, market_id: str) -> int:
        """Cancel everything for one market (e.g. at expiry)."""
        ids = [o.order_id for o in self.live.values() if o.market_id == market_id]
        if not ids:
            return 0
        # Async fire-and-forget
        asyncio.create_task(
            self._async_cancel_market(market_id, ids),
            name=f"live_cancel_market_{market_id[:10]}",
        )
        return len(ids)

    async def _async_cancel_market(self, market_id: str, ids: list[str]) -> None:
        async with self._lock(market_id):
            n = await self.clob.cancel_orders(ids)
            self._cum_cancels += n
            for oid in ids:
                self.live.pop(oid, None)
            log.info("live_cancel_all_for_market", market=market_id[:10], n=n)

    def _token_for(self, market_id: str, side: str) -> str | None:
        for tid, (mid, s) in self._token_to_market_side.items():
            if mid == market_id and s == side:
                return tid
        return None

    # ── FILL-EVENT-DRIVEN path ──

    async def on_user_event(self, msg: dict) -> None:
        """Route user WS event to the right handler."""
        et = msg.get("event_type")
        if et == "trade":
            await self._on_trade(msg)
        elif et == "order":
            self._on_order_status(msg)

    async def _on_trade(self, msg: dict) -> None:
        """Real fill arrived. Update inventory, instant repost SAME level."""
        order_id = msg.get("order_id") or msg.get("orderID") or msg.get("orderId")
        # Polymarket WS uses 'asset_id' for the token in user trade events
        token_id = (
            msg.get("asset_id") or msg.get("asset") or msg.get("market")
        )
        # Note: msg has 'side' (BUY/SELL) and 'outcome' (Yes/No) but we
        # derive both from the token_id → market+side lookup below for
        # reliability (Polymarket field names vary across event types).
        try:
            price = float(msg.get("price", 0))
            size = int(float(msg.get("size", 0)))
        except (TypeError, ValueError):
            log.warning("trade_event_bad_numbers", msg=str(msg)[:200])
            return
        if not token_id or size <= 0 or price <= 0:
            return

        # Resolve market + side (YES/NO) from token
        market_side = self._token_to_market_side.get(token_id)
        if not market_side:
            log.debug("trade_for_unknown_token", token=token_id[:14])
            return
        market_id, side = market_side

        self._cum_fills += 1
        log.info(
            "live_fill",
            market=market_id[:10], side=side, price=price, size=size, order_id=str(order_id)[:14],
        )

        # Update internal tracking
        if order_id and order_id in self.live:
            o = self.live[order_id]
            if size >= o.size:
                del self.live[order_id]
            else:
                o.size -= size  # partial fill

        # Update inventory
        if self._on_fill is not None:
            try:
                await self._on_fill(market_id, side, price, size)
            except Exception as e:
                log.warning("on_fill_callback_error", error=str(e))

        # ⚡ INSTANT REPOST: same price, same size — fast back into queue
        await self.instant_repost(market_id, side, price, size)

    def _on_order_status(self, msg: dict) -> None:
        """Cancellation/expiration events drop the order from live state."""
        order_id = msg.get("id") or msg.get("order_id")
        status = (msg.get("status") or "").upper()
        if order_id and status in ("CANCELED", "CANCELLED", "EXPIRED", "REJECTED"):
            self.live.pop(order_id, None)

    async def instant_repost(
        self, market_id: str, side: str, price: float, size: int
    ) -> None:
        """Post a fresh limit at (side, price) right after a fill — keeps us
        in queue at that level. No diff/recompute; just one POST."""
        token_id = self._token_for(market_id, side)
        if not token_id:
            return
        resp = await self.clob.place_limit(
            token_id=token_id, price=price, size=size,
            side="BUY", post_only=True,
        )
        if resp is None:
            self._failed_posts += 1
            return
        oid = resp["order_id"]
        if not oid:
            self._failed_posts += 1
            return
        self.live[oid] = LiveOrder(
            order_id=oid, market_id=market_id, token_id=token_id,
            side=side, price=price, size=size, placed_at=time.time(),
        )
        self._cum_instant_reposts += 1
        log.info(
            "instant_repost",
            market=market_id[:10], side=side, price=price, size=size,
        )

    # ── Startup recovery ──

    async def cancel_all_open(self) -> int:
        """At startup: wipe all orphan orders left from previous session."""
        n = await self.clob.cancel_all()
        self.live.clear()
        return n

    async def restore_from_clob(self) -> int:
        """Pull currently-open orders and rebuild ``self.live`` (alternative
        to cancel_all_open if you want to adopt orphans instead)."""
        orders = await self.clob.get_open_orders()
        n = 0
        for o in orders:
            try:
                oid = o.get("id") or o.get("orderID")
                token = o.get("asset_id") or o.get("asset")
                price = float(o.get("price", 0))
                size = int(float(o.get("size_remaining") or o.get("original_size") or 0))
            except (TypeError, ValueError):
                continue
            if not (oid and token and price > 0 and size > 0):
                continue
            ms = self._token_to_market_side.get(token)
            if not ms:
                continue
            mid, side = ms
            self.live[oid] = LiveOrder(
                order_id=oid, market_id=mid, token_id=token,
                side=side, price=price, size=size, placed_at=time.time(),
            )
            n += 1
        log.info("restored_from_clob", n=n)
        return n

    # ── Metrics ──

    def stats(self) -> dict[str, int]:
        return {
            "sync_count": self._sync_count,
            "cumulative_posts": self._cum_posts,
            "cumulative_cancels": self._cum_cancels,
            "cumulative_fills": self._cum_fills,
            "cumulative_instant_reposts": self._cum_instant_reposts,
            "failed_posts": self._failed_posts,
            "live_markets": len({o.market_id for o in self.live.values()}),
            "live_quotes_total": len(self.live),
        }
