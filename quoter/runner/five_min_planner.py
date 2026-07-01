"""Pure planner for the 5m early-consistent-leader strategy.

Before minute 2: accumulate BOTH sides as maker bids, leaning `lean:1` into the
current leader (the side with the higher mid). At/after minute 2: apply the filter —
trade only if the leader was CONSISTENT (lead1 == lead2) AND its price is in the band.
On filter fail, post nothing (hold what was accumulated). Bounded by per_window_cap.
Pure, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FivePlan:
    orders: list           # list of (side, price, size) maker bids to post this tick
    passed: object         # bool after minute 2, else None


def plan_five_min(minute, lead1, lead2, lead_price2, spent, per_window_cap,
                  lean, band_lo, band_hi, rung_size, yes_mid, no_mid) -> FivePlan:
    passed = None
    if minute >= 2:
        passed = bool(lead1 is not None and lead1 == lead2
                      and band_lo <= lead_price2 <= band_hi)
        if not passed:
            return FivePlan([], passed)
    budget = per_window_cap - spent
    if budget <= 0 or yes_mid <= 0 or no_mid <= 0:
        return FivePlan([], passed)
    leader, lead_mid = ("YES", yes_mid) if yes_mid >= no_mid else ("NO", no_mid)
    lag, lag_mid = ("NO", no_mid) if leader == "YES" else ("YES", yes_mid)
    orders = []
    lead_size = float(rung_size * lean)
    if lead_size * lead_mid <= budget + 1e-9:
        orders.append((leader, round(lead_mid, 3), lead_size))
        budget -= lead_size * lead_mid
    lag_size = float(rung_size)
    if lag_size * lag_mid <= budget + 1e-9:
        orders.append((lag, round(lag_mid, 3), lag_size))
    return FivePlan(orders, passed)
