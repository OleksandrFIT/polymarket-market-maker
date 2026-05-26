"""Mirror of one Polymarket CLOB orderbook (per token_id).

Updated via WebSocket events: ``book`` (full snapshot) and ``price_change``
(deltas). Convention observed on Polymarket WS:

* ``bids`` arrive sorted ascending (lowest first), so the BEST bid is the
  last element.
* ``asks`` arrive sorted ascending (lowest first), so the BEST ask is the
  first element.
* In ``price_change`` events, ``size == "0"`` means level removal.

Internally we use ``SortedDict`` for O(log N) inserts and O(1) top-of-book.
For bids, keys are negated to keep descending order (highest first).
"""

from __future__ import annotations

from dataclasses import dataclass

from sortedcontainers import SortedDict


@dataclass(frozen=True)
class Top:
    """Top-of-book snapshot. Either side may have None sizes if empty."""

    bid_px: float | None
    bid_sz: float
    ask_px: float | None
    ask_sz: float

    @property
    def mid(self) -> float | None:
        if self.bid_px is None or self.ask_px is None:
            return None
        return (self.bid_px + self.ask_px) / 2.0

    @property
    def spread(self) -> float | None:
        if self.bid_px is None or self.ask_px is None:
            return None
        return self.ask_px - self.bid_px


class LocalBook:
    """Per-token orderbook mirror. Not thread-safe — owned by a single
    asyncio task (the WS dispatcher)."""

    __slots__ = ("_bids", "_asks", "_last_update_ts")

    def __init__(self) -> None:
        # Bid keys are NEGATED so iteration yields highest price first
        self._bids: SortedDict = SortedDict()
        self._asks: SortedDict = SortedDict()
        self._last_update_ts: float = 0.0

    @property
    def last_update_ts(self) -> float:
        return self._last_update_ts

    def apply_full(self, bids: list[dict], asks: list[dict], ts: float = 0.0) -> None:
        """Replace book with a fresh snapshot (``book`` event)."""
        self._bids.clear()
        self._asks.clear()
        for lvl in bids:
            try:
                p = float(lvl["price"])
                s = float(lvl["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if s > 0:
                self._bids[-p] = s
        for lvl in asks:
            try:
                p = float(lvl["price"])
                s = float(lvl["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if s > 0:
                self._asks[p] = s
        if ts > 0:
            self._last_update_ts = ts

    def apply_delta(self, msg: dict, ts: float = 0.0) -> None:
        """Apply ``price_change`` event. Removes levels with size==0."""
        for ch in msg.get("price_changes", []) or msg.get("changes", []):
            try:
                p = float(ch["price"])
                s = float(ch["size"])
                side = (ch.get("side") or "").upper()
            except (KeyError, TypeError, ValueError):
                continue
            if side == "BUY":
                key = -p
                if s == 0:
                    self._bids.pop(key, None)
                else:
                    self._bids[key] = s
            elif side == "SELL":
                if s == 0:
                    self._asks.pop(p, None)
                else:
                    self._asks[p] = s
        if ts > 0:
            self._last_update_ts = ts

    def top(self) -> Top | None:
        """Return best bid + best ask. None if BOTH sides are empty."""
        if not self._bids and not self._asks:
            return None
        bid_px: float | None = None
        bid_sz = 0.0
        ask_px: float | None = None
        ask_sz = 0.0
        if self._bids:
            bk = self._bids.keys()[0]
            bid_px = -bk
            bid_sz = self._bids[bk]
        if self._asks:
            ak = self._asks.keys()[0]
            ask_px = ak
            ask_sz = self._asks[ak]
        return Top(bid_px=bid_px, bid_sz=bid_sz, ask_px=ask_px, ask_sz=ask_sz)

    def depth(self, side: str, n: int = 5) -> list[tuple[float, float]]:
        """Return top-N levels of one side as ``[(price, size), ...]``.

        ``side`` is "BUY" (bids) or "SELL" (asks). Best level first.
        """
        if side.upper() == "BUY":
            out = []
            for i, k in enumerate(self._bids.keys()):
                if i >= n:
                    break
                out.append((-k, self._bids[k]))
            return out
        out = []
        for i, k in enumerate(self._asks.keys()):
            if i >= n:
                break
            out.append((k, self._asks[k]))
        return out

    def is_empty(self) -> bool:
        return not self._bids and not self._asks

    def __repr__(self) -> str:
        t = self.top()
        if t is None:
            return "LocalBook(empty)"
        return f"LocalBook(bid={t.bid_px}@{t.bid_sz:.0f}, ask={t.ask_px}@{t.ask_sz:.0f})"
