"""Multi-token book router.

Owns one ``LocalBook`` per subscribed token_id. Receives raw WS events from
``PolyMarketWS`` and routes them to the right book. Listeners (e.g. quoter
loop) can subscribe per-token to receive "this token's book changed"
notifications.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from quoter.book.local_book import LocalBook, Top
from quoter.ops.logger import get_logger

log = get_logger("book_manager")

DirtyCallback = Callable[[str], Awaitable[None]]
"""``async def on_dirty(token_id: str)`` — fired after a book update."""


class BookManager:
    """Routes WS events to per-token ``LocalBook`` instances.

    Usage:
        bm = BookManager()
        bm.subscribe(token_id, my_callback)
        # then feed it via:
        await bm.on_ws_event(msg)
    """

    def __init__(self) -> None:
        self._books: dict[str, LocalBook] = {}
        self._listeners: dict[str, list[DirtyCallback]] = {}

    def book(self, token_id: str) -> LocalBook:
        """Get-or-create a book for ``token_id``."""
        b = self._books.get(token_id)
        if b is None:
            b = LocalBook()
            self._books[token_id] = b
        return b

    def top(self, token_id: str) -> Top | None:
        b = self._books.get(token_id)
        return b.top() if b is not None else None

    def subscribe(self, token_id: str, cb: DirtyCallback) -> None:
        """Register a callback fired after each book update for this token."""
        self._listeners.setdefault(token_id, []).append(cb)

    def unsubscribe_all(self, token_id: str) -> None:
        """Remove all listeners for a token (e.g. when market expires)."""
        self._listeners.pop(token_id, None)
        self._books.pop(token_id, None)

    async def on_ws_event(self, msg: dict) -> None:
        """Dispatch one WS event. Updates the matching book and fires listeners.

        Books are created on-demand for any token_id that appears in events.
        We only receive events for tokens we explicitly subscribed to via the
        WS connection, so unknown token_ids should not appear in practice.
        """
        event_type = msg.get("event_type")
        asset_id = msg.get("asset_id") or msg.get("market")
        if not asset_id:
            return
        book = self.book(asset_id)
        ts = _parse_ts(msg.get("timestamp"))
        if event_type == "book":
            book.apply_full(msg.get("bids", []), msg.get("asks", []), ts=ts)
        elif event_type == "price_change":
            book.apply_delta(msg, ts=ts)
        elif event_type == "last_trade_price":
            # Doesn't modify the book; ignore for now
            return
        else:
            return
        # Notify listeners
        for cb in self._listeners.get(asset_id, []):
            try:
                await cb(asset_id)
            except Exception as e:
                log.warning("listener_exception", token=asset_id[:16], error=str(e))


def _parse_ts(raw: Any) -> float:
    if raw is None:
        return 0.0
    try:
        # Polymarket sends ms-since-epoch as string in many events
        return float(raw) / 1000.0
    except (TypeError, ValueError):
        return 0.0
