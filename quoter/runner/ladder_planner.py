"""Pure laddered re-quoting brain — lays a deep ladder of bids both sides. No I/O.

Catches cheap dips (the merge edge the competitor analysis proved real) by resting
rungs below the mid. Anchor is a config toggle: "entry" keeps rungs static at the
window-entry mid (a dip into a low rung fills cheap); "book" chases the current best
bid. A hard naked-cap pulls the heavier side's rungs so we never accumulate a large
directional position (the competitor's losing part). The per-rung cost-basis gate
reuses the over-buy fix: no rung may complete a held leg into a >= $1 pair.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.strategy.ladder import Quote, Side
from quoter.runner.requote_planner import RestingOrder


@dataclass
class LadderPlan:
    cancels: list[str] = field(default_factory=list)
    posts: list[Quote] = field(default_factory=list)


def _rung_prices(anchor: float, ask: float | None, delta: float,
                 rungs: int, spacing: float) -> list[float]:
    """Descending rung prices from (anchor - delta), clamped 0.01 .. ask-0.01."""
    top = round(anchor - delta, 2)
    if ask and ask > 0:
        top = min(top, round(ask - 0.01, 2))
    out: list[float] = []
    for i in range(rungs):
        p = round(top - i * spacing, 2)
        if p >= 0.01:
            out.append(p)
    return out


def plan_ladder(
    *,
    yes_bid: float,
    no_bid: float,
    yes_ask: float | None,
    no_ask: float | None,
    entry_mid: float,
    inv_yes: int,
    inv_no: int,
    yes_cost: float,
    no_cost: float,
    committed: float,
    resting: dict[str, list[RestingOrder]],
    cfg: Config,
    trend_bias: str = "NEUTRAL",
    suppressed: frozenset[str] = frozenset(),
) -> LadderPlan:
    """Return the (cancels, posts) ladder plan for this tick. Pure + deterministic."""
    plan = LadderPlan()

    if committed >= cfg.per_window_cap:
        for side in ("YES", "NO"):
            for ro in resting.get(side, []):
                plan.cancels.append(ro.order_id)
        return plan

    delta = cfg.merge_edge / 2.0
    if cfg.ladder_anchor == "book":
        anchor_yes, anchor_no = yes_bid, no_bid
    else:
        anchor_yes, anchor_no = entry_mid, (1.0 - entry_mid)

    yes_rungs = _rung_prices(anchor_yes, yes_ask, delta, cfg.rungs, cfg.rung_spacing)
    no_rungs = _rung_prices(anchor_no, no_ask, delta, cfg.rungs, cfg.rung_spacing)

    yes_avg = (yes_cost / inv_yes) if inv_yes > 0 else None
    no_avg = (no_cost / inv_no) if inv_no > 0 else None
    naked = inv_yes - inv_no
    target = cfg.rungs * cfg.rung_size

    def pair_ok(side: Side, price: float) -> bool:
        if side == "YES":
            other = no_avg if (naked < 0 and no_avg is not None) else (no_rungs[0] if no_rungs else 1.0)
            return (price + other) < 1.0
        other = yes_avg if (naked > 0 and yes_avg is not None) else (yes_rungs[0] if yes_rungs else 1.0)
        return (price + other) < 1.0

    # HARD naked cap (lag-proof). Desire only as many rungs per side as can fill WITHOUT
    # pushing the naked past naked_cap — accounting for the RUNG SIZE (the old `naked <
    # cap` check let a rung fill at naked=cap-1 and overshoot by a full rung). The slot
    # count uses the credited naked; during the credit grace a just-filled rung still sits
    # in `resting` at the same (lagged) desired price, so the diff keeps it and we never
    # re-post into the lag. Staged: also never more than max_inflight_rungs in flight.
    mif = cfg.max_inflight_rungs
    yd = int(inv_yes // cfg.rung_size)
    nd = int(inv_no // cfg.rung_size)
    yes_slots = max(0, min(mif, (cfg.naked_cap - naked) // cfg.rung_size,
                           (target - inv_yes) // cfg.rung_size))
    no_slots = max(0, min(mif, (cfg.naked_cap + naked) // cfg.rung_size,
                          (target - inv_no) // cfg.rung_size))
    desired: dict[str, list[float]] = {"YES": [], "NO": []}
    desired["YES"] = [p for p in yes_rungs[yd:yd + yes_slots] if pair_ok("YES", p)]
    desired["NO"] = [p for p in no_rungs[nd:nd + no_slots] if pair_ok("NO", p)]

    # Trend detector: suppress the losing side's rungs (sit out the trend).
    if trend_bias == "UP":
        desired["NO"] = []     # Up winning → Down is the loser
    elif trend_bias == "DOWN":
        desired["YES"] = []    # Down winning → Up is the loser

    # Auto-flat: a side that was flattened this window is suppressed — post nothing
    # (no rebuy → no churn, and stop loading the losing side).
    for s in suppressed:
        desired[s] = []

    for side in ("YES", "NO"):
        want = set(desired[side])
        have_prices: set[float] = set()
        for ro in resting.get(side, []):
            if ro.price in want:
                have_prices.add(ro.price)
            else:
                plan.cancels.append(ro.order_id)
        for p in desired[side]:
            if p not in have_prices:
                plan.posts.append(Quote(side, p, cfg.rung_size))
    return plan
