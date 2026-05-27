"""Rolling price buffer with velocity computation.

Stores last N seconds of (timestamp, price) tuples for one asset.
Computes velocity over arbitrary lookback windows — used as PREDICTIVE
directional signal (e.g. BTC moving up over last 30s → bet YES side).

Bonereaper uses signals like this to direct his conviction trades. Our
``ladder.compute_ladder`` calls these to decide:
  * directional_skew: only skew when mid AND velocity AGREE
  * conviction: trigger big sizing only with confirmed velocity
"""

from __future__ import annotations

import time
from collections import deque


class PriceBuffer:
    """Append-only ring buffer of (ts, price) for one asset.

    Old entries auto-evicted when their age exceeds ``max_age_sec``.
    """

    __slots__ = ("_buf", "_max_age", "_last_price")

    def __init__(self, max_age_sec: float = 300.0) -> None:
        self._buf: deque[tuple[float, float]] = deque()
        self._max_age = max_age_sec
        self._last_price: float | None = None

    def add(self, price: float, ts: float | None = None) -> None:
        """Append a new price observation. Evicts entries older than ``max_age``."""
        if price <= 0:
            return
        now = ts if ts is not None else time.time()
        self._buf.append((now, float(price)))
        self._last_price = float(price)
        cutoff = now - self._max_age
        while self._buf and self._buf[0][0] < cutoff:
            self._buf.popleft()

    @property
    def latest(self) -> float | None:
        """Most recent price (or None if buffer empty)."""
        return self._last_price

    def price_at_age(self, age_sec: float) -> float | None:
        """Return the price observation closest to ``age_sec`` ago.

        If we have no observation that old, returns the oldest available.
        Returns None if buffer is empty.
        """
        if not self._buf:
            return None
        target_ts = self._buf[-1][0] - age_sec
        # Linear scan from front (small buffers — O(N) is fine)
        best = self._buf[0]
        for entry in self._buf:
            if entry[0] <= target_ts:
                best = entry
            else:
                break
        return best[1]

    def velocity(self, lookback_sec: float) -> float | None:
        """Relative price change vs ``lookback_sec`` ago.

        Returns ``(latest - past) / past`` as a fraction (e.g. 0.001 = 0.1% up).
        Returns None if we don't have enough history.
        """
        if self._last_price is None or len(self._buf) < 2:
            return None
        past = self.price_at_age(lookback_sec)
        if past is None or past <= 0:
            return None
        return (self._last_price - past) / past

    def age_span_sec(self) -> float:
        """How many seconds of history we have."""
        if len(self._buf) < 2:
            return 0.0
        return self._buf[-1][0] - self._buf[0][0]

    def __len__(self) -> int:
        return len(self._buf)


class MultiAssetPriceBuffer:
    """Convenience: one PriceBuffer per asset string."""

    def __init__(self, assets: tuple[str, ...], max_age_sec: float = 300.0) -> None:
        self._buffers: dict[str, PriceBuffer] = {
            a: PriceBuffer(max_age_sec) for a in assets
        }

    def add(self, asset: str, price: float, ts: float | None = None) -> None:
        buf = self._buffers.get(asset)
        if buf is None:
            buf = PriceBuffer()
            self._buffers[asset] = buf
        buf.add(price, ts)

    def velocity(self, asset: str, lookback_sec: float) -> float | None:
        buf = self._buffers.get(asset)
        return buf.velocity(lookback_sec) if buf else None

    def latest(self, asset: str) -> float | None:
        buf = self._buffers.get(asset)
        return buf.latest if buf else None

    def __getitem__(self, asset: str) -> PriceBuffer:
        return self._buffers.setdefault(asset, PriceBuffer())
