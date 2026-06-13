# quoter/runner/fill_inventory.py
"""Pure: rebuild per-side inventory from the window's REAL fills (ground truth),
replacing the live loop's old "order vanished ⇒ assume filled" heuristic that
produced phantom fills. Stateless — recompute from the full fill list each tick,
so it never drifts and self-corrects as the fills feed settles.

A fill is {"side": "YES"|"NO", "action": "BUY"|"SELL", "size": float, "price": float}.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Side = str  # "YES" | "NO"


@dataclass
class Inventory:
    inv: dict = field(default_factory=lambda: {"YES": 0, "NO": 0})
    cost: dict = field(default_factory=lambda: {"YES": 0.0, "NO": 0.0})
    buy_qty: dict = field(default_factory=lambda: {"YES": 0, "NO": 0})

    def avg(self, side: Side) -> float | None:
        """Average BUY price of the side (cost basis of held shares); None if no buys."""
        return self.cost[side] / self.buy_qty[side] if self.buy_qty[side] > 0 else None


def inventory_from_fills(fills: list[dict]) -> Inventory:
    """Net inventory = BUY size − SELL size per side; cost = BUY cost basis."""
    out = Inventory()
    for f in fills:
        side = f["side"]
        if side not in ("YES", "NO"):
            continue
        size = f["size"]
        if f["action"] == "BUY":
            out.inv[side] += size
            out.cost[side] += size * f["price"]
            out.buy_qty[side] += size
        elif f["action"] == "SELL":
            out.inv[side] -= size
    return out
