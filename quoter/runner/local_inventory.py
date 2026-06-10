"""Local, optimistic inventory accounting for the live re-quoting loop.

The bug it fixes (the live over-buy: 15 Up vs target 5, naked 9 vs cap 5):
the on-chain / API share read LAGS our fills by several seconds, but the live
re-quote loop runs every ~2s. Reading stale inventory, the loop kept re-posting
a side it had ALREADY filled, so the per-side target, the naked cap, and the
capital cap all failed together — every one of them was derived from the same
lagging read. The pure planner (``plan_requote``) and its synchronous simulator
are correct; they simply never modelled read-lag.

Fix: account a fill LOCALLY the instant our resting order vanishes without us
cancelling it (it filled), regardless of what the lagging chain read says. Only
ever reconcile the chain read UPWARD (partial fills, or anything external) —
never downward, because a low read is almost always lag, and under-counting our
own fills is exactly what caused the over-buy. With an accurate local count, the
target / naked / capital caps in ``plan_requote`` finally bind live.

Pure + fully unit-testable — no network, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Side = str  # "YES" | "NO"


@dataclass
class LocalInventory:
    """Optimistic per-side share + cost tracker. Monotonic in inventory: it only
    ever goes up (we hold to resolution and never sell mid-window), so a lagging
    low read can never trick the loop into re-buying a leg it already owns."""

    inv: dict = field(default_factory=lambda: {"YES": 0, "NO": 0})
    cost: dict = field(default_factory=lambda: {"YES": 0.0, "NO": 0.0})

    def credit_fill(self, side: Side, size: int, price: float) -> None:
        """Our resting order vanished and we did NOT cancel it → it filled.
        Credit the shares + their exact paid cost immediately, before the
        on-chain read catches up. This is what stops the over-buy: the very next
        tick sees the higher inventory and the planner stops re-posting."""
        self.inv[side] += size
        self.cost[side] += size * price

    def reconcile_up(self, side: Side, chain_inv: int, est_price: float) -> None:
        """Raise the local count to the chain read when the chain is higher
        (partial fills, or shares we didn't post ourselves). NEVER lower it — a
        low read is lag, and lowering is what produced the live over-buy.
        ``est_price`` prices the shares we hadn't already accounted for."""
        if chain_inv > self.inv[side]:
            diff = chain_inv - self.inv[side]
            self.cost[side] += diff * est_price
            self.inv[side] = chain_inv

    def avg(self, side: Side) -> float | None:
        """Average paid price on a held side (cost basis for the edge gate)."""
        return self.cost[side] / self.inv[side] if self.inv[side] > 0 else None
