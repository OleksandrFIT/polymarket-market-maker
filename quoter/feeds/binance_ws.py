"""Binance trade WebSocket: latest spot price per asset.

Uses Binance combined-streams endpoint (one connection for all assets).
Pure async, no blocking. Reconnects with exponential backoff.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import orjson
import websockets

from quoter.ops.logger import get_logger

log = get_logger("binance_ws")

# Asset symbol → Binance pair name
_SYMBOL_MAP: dict[str, str] = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "XRP": "xrpusdt",
}

PriceCallback = Callable[[str, float, float], Awaitable[None]]
"""``async def on_price(asset: str, price_usd: float, ts: float)``"""


class BinanceWS:
    """Subscribes to ``@trade`` stream for given assets.

    Each trade tick is dispatched to ``on_price(asset, price, ts)``.
    Single TCP connection multiplexed across all assets.
    """

    def __init__(
        self,
        assets: tuple[str, ...],
        on_price: PriceCallback,
        backoff_max_sec: float = 30.0,
    ) -> None:
        self._assets = tuple(a for a in assets if a in _SYMBOL_MAP)
        if not self._assets:
            raise ValueError(f"No supported assets in {assets!r}; known: {list(_SYMBOL_MAP)}")
        self._on_price = on_price
        self._backoff_max = backoff_max_sec
        self._url = self._build_url()
        self._last_msg_ts: float = 0.0

    def _build_url(self) -> str:
        streams = [f"{_SYMBOL_MAP[a]}@trade" for a in self._assets]
        return f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"

    @property
    def last_msg_ts(self) -> float:
        return self._last_msg_ts

    async def run(self) -> None:
        """Run forever; reconnects on disconnect. Cancel the task to stop."""
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    self._url,
                    compression=None,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**20,
                ) as ws:
                    log.info("binance_ws_connected", assets=list(self._assets))
                    backoff = 1.0
                    async for raw in ws:
                        msg = orjson.loads(raw)
                        await self._handle(msg)
            except asyncio.CancelledError:
                log.info("binance_ws_cancelled")
                raise
            except Exception as e:
                log.warning("binance_ws_disconnect", error=str(e), backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._backoff_max)

    async def _handle(self, msg: dict) -> None:
        # Combined-stream format: {"stream": "btcusdt@trade", "data": {...trade fields}}
        data = msg.get("data") if isinstance(msg, dict) else None
        if not data:
            return
        symbol_lc = (data.get("s") or "").lower()
        price_str = data.get("p")
        ts_ms = data.get("T")
        if not (symbol_lc and price_str and ts_ms):
            return
        # Reverse-map symbol → asset
        for asset, sym in _SYMBOL_MAP.items():
            if sym == symbol_lc:
                try:
                    price = float(price_str)
                    ts = float(ts_ms) / 1000.0
                except (TypeError, ValueError):
                    return
                self._last_msg_ts = ts
                await self._on_price(asset, price, ts)
                return
