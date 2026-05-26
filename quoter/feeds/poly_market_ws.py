"""Polymarket CLOB market WebSocket: book updates + price changes + trades.

Subscribed assets (token IDs) are mutable via ``set_subscriptions``; the
underlying connection is rebuilt on next reconnect when the set changes.

Three event types received:
  * ``book``             — full orderbook snapshot (initial + periodic)
  * ``price_change``     — delta updates
  * ``last_trade_price`` — fill prints

Polymarket sends the FIRST message as a JSON list (batch of initial state),
subsequent messages as dicts. This module normalizes both.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import orjson
import websockets

from quoter.ops.logger import get_logger

log = get_logger("poly_market_ws")

EventCallback = Callable[[dict], Awaitable[None]]
"""``async def on_event(msg: dict)`` — single normalized message."""


class PolyMarketWS:
    """Subscribes to N token_ids and dispatches events to one callback.

    Token list is set via ``set_subscriptions`` BEFORE ``run`` is called.
    Calling ``set_subscriptions`` while connected closes the socket so the
    reconnect loop picks up the new subscription list on next attempt.
    """

    def __init__(
        self,
        url: str,
        on_event: EventCallback,
        backoff_max_sec: float = 30.0,
    ) -> None:
        self._url = url
        self._on_event = on_event
        self._backoff_max = backoff_max_sec
        self._token_ids: list[str] = []
        self._ws: websockets.ClientConnection | None = None
        self._last_msg_ts: float = 0.0

    def set_subscriptions(self, token_ids: list[str]) -> None:
        """Replace token_id list. If a connection is open, drop it so next
        reconnect picks up the new list."""
        self._token_ids = list(token_ids)
        if self._ws is not None:
            # Schedule close; reconnect loop will rebuild
            try:
                asyncio.create_task(self._ws.close())
            except Exception:
                pass

    @property
    def last_msg_ts(self) -> float:
        """Monotonic-ish: server-supplied timestamp of latest message (sec)."""
        return self._last_msg_ts

    async def run(self) -> None:
        """Run forever; reconnects on disconnect."""
        backoff = 1.0
        while True:
            if not self._token_ids:
                # Nothing to subscribe — wait and retry
                await asyncio.sleep(1.0)
                continue
            try:
                async with websockets.connect(
                    self._url,
                    compression=None,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**20,
                ) as ws:
                    self._ws = ws
                    sub = {"type": "market", "assets_ids": self._token_ids}
                    await ws.send(orjson.dumps(sub).decode())
                    log.info("poly_market_ws_connected", n_tokens=len(self._token_ids))
                    backoff = 1.0
                    async for raw in ws:
                        await self._handle_raw(raw)
            except asyncio.CancelledError:
                log.info("poly_market_ws_cancelled")
                raise
            except Exception as e:
                log.warning("poly_market_ws_disconnect", error=str(e), backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._backoff_max)
            finally:
                self._ws = None

    async def _handle_raw(self, raw: str | bytes) -> None:
        try:
            parsed = orjson.loads(raw)
        except Exception:
            log.warning("poly_ws_unparseable", raw=str(raw)[:200])
            return
        # Normalize: server sends either dict or list-of-dicts (initial batch)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    await self._dispatch(item)
        elif isinstance(parsed, dict):
            await self._dispatch(parsed)

    async def _dispatch(self, msg: dict) -> None:
        # Server timestamp is in ms as a string in most events
        ts_raw = msg.get("timestamp")
        if ts_raw is not None:
            try:
                self._last_msg_ts = float(ts_raw) / 1000.0
            except (TypeError, ValueError):
                pass
        await self._on_event(msg)
