"""Pure planner: how many favorite shares to taker-buy this tick (directional tilt).

Accumulate the side BTC is trending toward. Sizing targets a $-fraction of the
window cap (favorite $ ≈ tilt_frac * per_window_cap) — directional-first and
scale-correct; the old favorite_shares≈spent rule collapsed at small budgets
where spent$ ≈ favorite share-count. Capped per tick by `step` and per window by
remaining budget. Pure, no I/O.
"""
from __future__ import annotations


def plan_tilt(tbias: str, fav_ask: float | None, fav_cost: float, spent: float,
              per_window_cap: float, tilt_frac: float, step: int,
              max_price: float = 0.90) -> float:
    """Whole favorite shares to taker-buy now, toward a $-fraction target.

    Sizes the favorite position to ~`tilt_frac` of the window cap (scale-correct;
    the old favorite_shares≈spent rule collapsed at small budgets where spent$ ≈
    favorite share-count). Capped per tick by `step` and by remaining window budget.

    tbias: "UP" | "DOWN" | "NEUTRAL". Only UP/DOWN act.
    fav_ask: favorite-side ask (taker entry). None / <=0 / >=1 -> 0.
    fav_cost: $ already deployed in the favorite this window.
    spent: total $ committed this window so far.
    per_window_cap: $ ceiling for the window.
    tilt_frac: target favorite $ as a fraction of per_window_cap (e.g. 0.65).
    step: max shares to add this tick.
    max_price: don't chase the favorite above this ask.
    """
    if tbias not in ("UP", "DOWN"):
        return 0.0
    if fav_ask is None or fav_ask <= 0.0 or fav_ask >= 1.0:
        return 0.0
    if fav_ask > max_price:
        return 0.0
    target_usd = tilt_frac * per_window_cap          # directional-first $ target
    gap_usd = target_usd - fav_cost
    if gap_usd <= 0:
        return 0.0
    q = gap_usd / fav_ask
    q = min(q, float(step))
    budget_left = per_window_cap - spent
    if budget_left <= 0:
        return 0.0
    q = min(q, budget_left / fav_ask)
    return float(int(q))                              # whole shares
