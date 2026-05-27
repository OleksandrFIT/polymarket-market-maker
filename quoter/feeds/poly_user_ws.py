"""Polymarket User WebSocket — receives MY OWN order events in real time.

Subscribes to the authenticated ``wss://...ws/user`` channel and dispatches
each ``trade`` / ``order`` event to a callback. This is the foundation of
*swift fill-detect*: real fills arrive in <150ms instead of the 1-30s of
REST polling.

Two event types we care about:
  * ``trade`` (our order was filled, fully or partially):
      {"event_type": "trade", "order_id": "0x...", "market": "...",
       "side": "BUY"|"SELL", "outcome": "Yes"|"No",
       "price": float, "size": int, ...}

  * ``order`` (status update — placed, canceled, expired):
      {"event_type": "order", "id": "0x...", "status": "CANCELED"|"OPEN"|..., ...}

Used only in LIVE mode. Paper/shadow modes don't need it (no real orders).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import orjson
import websockets

from quoter.creds import PolyCreds
from quoter.ops.logger import get_logger

log = get_logger("poly_user_ws")

EventCallback = Callable[[dict], Awaitable[None]]
"""``async def on_user_event(msg: dict)`` — one normalized event."""


class PolyUserWS:
    """Subscribes to authenticated user channel.

    Reconnects with exponential backoff on disconnect. Authentication is
    sent on every connect; if creds are wrong server drops the socket.
    """

    def __init__(
        self,
        url: str,
        creds: PolyCreds,
        on_event: EventCallback,
        backoff_max_sec: float = 30.0,
    ) -> None:
        self._url = url
        self._creds = creds
        self._on_event = on_event
        self._backoff_max = backoff_max_sec
        self._last_msg_ts: float = 0.0
        self._event_count: int = 0

    @property
    def last_msg_ts(self) -> float:
        return self._last_msg_ts

    @property
    def event_count(self) -> int:
        return self._event_count

    async def run(self) -> None:
        """Run forever; reconnect on disconnect."""
        backoff = 1.0
        sub = {
            "auth": self._creds.auth_dict(),
            "type": "user",
            "markets": [],  # empty = subscribe to ALL user activity
        }
        while True:
            try:
                async with websockets.connect(
                    self._url,
                    compression=None,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**20,
                ) as ws:
                    await ws.send(orjson.dumps(sub).decode())
                    log.info("poly_user_ws_connected")
                    backoff = 1.0
                    async for raw in ws:
                        await self._handle_raw(raw)
            except asyncio.CancelledError:
                log.info("poly_user_ws_cancelled")
                raise
            except Exception as e:
                log.warning("poly_user_ws_disconnect", error=str(e), backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._backoff_max)

    async def _handle_raw(self, raw: str | bytes) -> None:
        try:
            parsed = orjson.loads(raw)
        except Exception:
            log.warning("poly_user_ws_unparseable", raw=str(raw)[:200])
            return
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    await self._dispatch(item)
        elif isinstance(parsed, dict):
            await self._dispatch(parsed)

    async def _dispatch(self, msg: dict) -> None:
        import time
        self._last_msg_ts = time.time()
        self._event_count += 1
        await self._on_event(msg)
