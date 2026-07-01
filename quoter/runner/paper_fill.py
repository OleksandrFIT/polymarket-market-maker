"""Optimistic maker-fill estimator for dry-run paper PnL.

A resting BUY bid at price P on a side fills when that side's price touches <= P in a
later tick (a seller crosses to our bid). `fill_frac` haircuts the credited size for
queue/latency (1.0 = optimistic, fills fully on touch). Pure, no I/O. This is an
ESTIMATE — real maker fills may be worse; only a small live run settles it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Bid:
    side: str
    price: float
    remaining: float


class PaperBook:
    def __init__(self, fill_frac: float = 1.0) -> None:
        self.fill_frac = fill_frac
        self._bids: list[_Bid] = []
        self.inv = {"YES": 0.0, "NO": 0.0}
        self.cost = {"YES": 0.0, "NO": 0.0}

    def post(self, side: str, price: float, size: float) -> None:
        self._bids.append(_Bid(side, float(price), float(size)))

    def spent(self) -> float:
        return self.cost["YES"] + self.cost["NO"]

    def on_tick(self, side: str, price) -> float:
        """Fill resting bids on `side` when `price` (best offer / traded) <= bid price."""
        if price is None:
            return 0.0
        filled = 0.0
        for b in self._bids:
            if b.side == side and b.remaining > 0 and price <= b.price:
                q = float(int(b.remaining * self.fill_frac))
                if q > 0:
                    b.remaining -= q
                    self.inv[side] += q
                    self.cost[side] += q * b.price
                    filled += q
        return filled
