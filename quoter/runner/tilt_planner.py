"""Pure planner: how many favorite shares to taker-buy this tick (directional tilt).

Accumulate the side BTC is trending toward. Sizing targets favorite_shares ≈ spent
(winning favorite ≈ breaks even on the matched part, profits on the edge), capped
per tick by `step` and per window by remaining budget. Pure, no I/O.
"""
from __future__ import annotations


def plan_tilt(tbias: str, fav_ask: float | None, inv_fav: float, spent: float,
              per_window_cap: float, step: int, max_price: float = 0.90) -> float:
    """Whole favorite shares to buy now; 0 when no tilt is warranted.

    tbias: "UP" | "DOWN" | "NEUTRAL". Only UP/DOWN act.
    fav_ask: favorite-side ask (taker entry). None / <=0 / >=1 → 0.
    inv_fav: favorite shares already held.
    spent: $ committed this window so far.
    per_window_cap: $ ceiling for the window.
    step: max shares to add this tick.
    max_price: don't chase the favorite above this ask.
    """
    if tbias not in ("UP", "DOWN"):
        return 0.0
    if fav_ask is None or fav_ask <= 0.0 or fav_ask >= 1.0:
        return 0.0
    if fav_ask > max_price:
        return 0.0
    gap = spent - inv_fav                 # target favorite_shares ≈ spent
    if gap <= 0:
        return 0.0
    q = gap / (1.0 - fav_ask)             # inv_fav + q == spent + q*fav_ask
    q = min(q, float(step))
    budget_left = per_window_cap - spent
    if budget_left <= 0:
        return 0.0
    q = min(q, budget_left / fav_ask)
    return float(int(q))                  # whole shares
