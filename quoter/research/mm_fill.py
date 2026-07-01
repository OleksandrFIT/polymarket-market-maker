"""Pure maker-fill model for the MM simulator. A resting BID fills from crossing taker
SELLs, queue-haircut by theta.fill. We NEVER post asks (the tactic never sells)."""
from __future__ import annotations

from quoter.research.mm_types import FillResult, Theta

_OI = {0: "Up", 1: "Down"}


def fill(side: str, price: float, size: float, tape_slice: list, theta: Theta) -> FillResult:
    remaining = float(size)
    filled = 0.0
    for t in tape_slice:
        if remaining <= 0:
            break
        if t["side"] != "SELL":
            continue
        if _OI.get(t["oi"]) != side:
            continue
        if t["price"] > price:
            continue
        take = min(remaining, float(t["size"]) * theta.fill)
        if take <= 0:
            continue
        filled += take
        remaining -= take
    return FillResult(filled=filled, avg_price=price if filled > 0 else 0.0)
