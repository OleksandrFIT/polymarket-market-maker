"""Per-market position tracking + realized P&L accounting.

Pure state, no I/O. Fed by ``OrderManager.on_fill`` (later phases) and
resolution events. Tracks YES/NO holdings cost-basis-style.

Three lifecycle events alter inventory:

* ``on_fill``    — a quote was hit (BUY). Adds shares to position.
* ``on_merge``   — matched YES+NO pair burned for $1 USDC each.
* ``on_resolve`` — market closed; winning side pays $1, losing $0.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

Side = Literal["YES", "NO"]


@dataclass
class Position:
    """Holdings + cost basis for one market."""

    yes_qty: int = 0
    no_qty: int = 0
    yes_cost_total: float = 0.0  # USDC spent on YES so far
    no_cost_total: float = 0.0

    @property
    def yes_avg(self) -> float:
        return self.yes_cost_total / self.yes_qty if self.yes_qty > 0 else 0.0

    @property
    def no_avg(self) -> float:
        return self.no_cost_total / self.no_qty if self.no_qty > 0 else 0.0

    @property
    def matched(self) -> int:
        """Number of YES-NO pairs that could be merged for $1 USDC each."""
        return min(self.yes_qty, self.no_qty)

    @property
    def net_yes_minus_no(self) -> int:
        return self.yes_qty - self.no_qty

    @property
    def total_cost(self) -> float:
        return self.yes_cost_total + self.no_cost_total


@dataclass
class Inventory:
    """All open positions plus running realized P&L."""

    positions: dict[str, Position] = field(default_factory=lambda: defaultdict(Position))
    realized_pnl: float = 0.0
    n_fills: int = 0
    n_merges: int = 0
    n_resolutions: int = 0

    # ── Event handlers ──

    def on_fill(self, market_id: str, side: Side, price: float, qty: int) -> None:
        """Record a new BUY fill at ``price`` for ``qty`` shares."""
        if qty <= 0 or price <= 0:
            return
        p = self.positions[market_id]
        if side == "YES":
            p.yes_qty += qty
            p.yes_cost_total += price * qty
        else:
            p.no_qty += qty
            p.no_cost_total += price * qty
        self.n_fills += 1

    def on_merge(self, market_id: str, pairs: int) -> None:
        """Convert ``pairs`` matched YES+NO into ``$pairs`` USDC.

        Capped at current ``matched`` count; safe to call with overshoot.
        """
        p = self.positions.get(market_id)
        if p is None:
            return
        actual = min(pairs, p.matched)
        if actual <= 0:
            return
        # Cost we sunk into these pairs = pairs * (yes_avg + no_avg)
        # Revenue from merge = pairs * $1.00
        cost = actual * (p.yes_avg + p.no_avg)
        revenue = actual * 1.0
        self.realized_pnl += revenue - cost
        # Reduce inventory at the OLD average price (matched pairs vanish)
        yes_avg = p.yes_avg
        no_avg = p.no_avg
        p.yes_qty -= actual
        p.no_qty -= actual
        p.yes_cost_total -= actual * yes_avg
        p.no_cost_total -= actual * no_avg
        self.n_merges += 1

    def on_resolve(self, market_id: str, winning_side: Side) -> None:
        """Resolve market: ``winning_side`` shares pay $1, opposite pays $0."""
        p = self.positions.pop(market_id, None)
        if p is None:
            return
        win_qty = p.yes_qty if winning_side == "YES" else p.no_qty
        cost = p.total_cost
        revenue = float(win_qty)  # $1 each
        self.realized_pnl += revenue - cost
        self.n_resolutions += 1

    # ── Queries ──

    def snapshot(self) -> dict:
        """JSON-safe view for logging / metrics endpoint."""
        return {
            "realized_pnl": round(self.realized_pnl, 4),
            "n_fills": self.n_fills,
            "n_merges": self.n_merges,
            "n_resolutions": self.n_resolutions,
            "open_markets": len(self.positions),
            "positions": {
                mid: {
                    "yes_qty": p.yes_qty,
                    "no_qty": p.no_qty,
                    "yes_avg": round(p.yes_avg, 4),
                    "no_avg": round(p.no_avg, 4),
                    "matched": p.matched,
                    "net": p.net_yes_minus_no,
                    "total_cost": round(p.total_cost, 2),
                }
                for mid, p in self.positions.items()
            },
        }

    def total_open_cost(self) -> float:
        return sum(p.total_cost for p in self.positions.values())
