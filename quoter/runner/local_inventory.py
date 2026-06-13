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
    """Optimistic per-side share + cost tracker. Inventory rises on fills
    (``credit_fill``) and a lagging low chain read can never trick the loop into
    re-buying a leg it already owns (``reconcile_up`` only ever raises). The one
    intentional decrease is ``debit_fill`` — the auto-flat sell of the naked
    excess; after a flatten the loop must skip ``reconcile_up`` on that side (the
    chain read lags HIGH and would otherwise re-add the sold shares)."""

    inv: dict = field(default_factory=lambda: {"YES": 0, "NO": 0})
    cost: dict = field(default_factory=lambda: {"YES": 0.0, "NO": 0.0})
    _over_since: dict = field(default_factory=lambda: {"YES": None, "NO": None})

    def credit_fill(self, side: Side, size: int, price: float) -> None:
        """Our resting order vanished and we did NOT cancel it → it filled.
        Credit the shares + their exact paid cost immediately, before the
        on-chain read catches up. This is what stops the over-buy: the very next
        tick sees the higher inventory and the planner stops re-posting."""
        self.inv[side] += size
        self.cost[side] += size * price

    def debit_fill(self, side: Side, size: int, price: float) -> None:
        """We SOLD ``size`` shares of ``side`` (auto-flat). Reduce inventory and
        its cost basis proportionally (by the average cost, so the remaining
        shares keep their basis). This is the ONE place inventory goes DOWN — only
        the naked excess is ever sold, never the paired core, so the post-debit
        count stays >= the other side. Clamps at zero; never negative.

        ``price`` is the sale price (logging / symmetry with ``credit_fill``); the
        cost basis is reduced by the average, not the sale price — cash proceeds
        are tracked by the live loop's collateral read, not here."""
        size = min(size, self.inv[side])
        if size <= 0:
            return
        avg = self.cost[side] / self.inv[side]
        self.inv[side] -= size
        self.cost[side] -= size * avg
        if self.inv[side] <= 0:
            self.inv[side] = 0
            self.cost[side] = 0.0

    def reconcile_up(self, side: Side, chain_inv: int, est_price: float) -> None:
        """Raise the local count to the chain read when the chain is higher
        (partial fills, or shares we didn't post ourselves). NEVER lower it — a
        low read is lag, and lowering is what produced the live over-buy.
        ``est_price`` prices the shares we hadn't already accounted for."""
        if chain_inv > self.inv[side]:
            diff = chain_inv - self.inv[side]
            self.cost[side] += diff * est_price
            self.inv[side] = chain_inv

    def reconcile_down(self, side: Side, real_qty: int, now: float, grace: float) -> None:
        """Phantom kill. If the optimistic local count exceeds the REAL-fills count
        for longer than ``grace`` seconds, the excess credit was never confirmed by a
        real fill (it was a phantom — a vanished-but-unfilled order) → lower local to
        real. A genuine fill appears in the real feed within the feed lag (< grace),
        clearing the gap before this triggers, so real fills are never reversed.
        ``grace`` MUST exceed the real-fills feed lag."""
        if self.inv[side] <= real_qty:
            self._over_since[side] = None
            return
        if self._over_since[side] is None:
            self._over_since[side] = now
        elif now - self._over_since[side] >= grace:
            avg = self.cost[side] / self.inv[side] if self.inv[side] > 0 else 0.0
            self.inv[side] = real_qty
            self.cost[side] = real_qty * avg
            self._over_since[side] = None

    def avg(self, side: Side) -> float | None:
        """Average paid price on a held side (cost basis for the edge gate)."""
        return self.cost[side] / self.inv[side] if self.inv[side] > 0 else None
