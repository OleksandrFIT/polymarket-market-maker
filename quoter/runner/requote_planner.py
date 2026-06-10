"""Pure re-quoting brain — decides what to cancel/post each tick. No I/O.

Given the current book, our resting orders, and our inventory, return the
cancel/post plan that keeps us at the top of the book on the side(s) we need,
while NEVER exceeding the naked or capital caps. Fully unit- and simulation-
testable without any network or live trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.strategy.ladder import Quote, Side


@dataclass
class RestingOrder:
    order_id: str
    side: Side
    price: float
    size: int


@dataclass
class RequotePlan:
    cancels: list[str] = field(default_factory=list)   # order_ids to cancel
    posts: list[Quote] = field(default_factory=list)    # new orders to post


def plan_requote(
    *,
    yes_bid: float,
    no_bid: float,
    inv_yes: int,
    inv_no: int,
    yes_cost: float,
    no_cost: float,
    resting: dict[str, RestingOrder | None],
    cfg: Config,
) -> RequotePlan:
    """Return the (cancels, posts) plan for this tick. Pure + deterministic."""
    plan = RequotePlan()

    # Gate 1 — CAPITAL: spent the budget → pull everything, post nothing.
    if (yes_cost + no_cost) >= cfg.per_market_cap_usd:
        for ro in resting.values():
            if ro is not None:
                plan.cancels.append(ro.order_id)
        return plan

    # Top-of-book join prices (already maker; best bids sum < $1 by measurement).
    yes_px = round(yes_bid, 2)
    no_px = round(no_bid, 2)
    edge_ok = (yes_px + no_px) < 1.0  # Gate 2 — EDGE: pair must cost < $1

    # Gate 3 — BALANCE: only quote the side we are NOT already long of past the
    # cap, so re-quoting actively drives naked toward zero (never past the cap).
    naked = inv_yes - inv_no
    want = {
        "YES": edge_ok and (naked < cfg.max_naked_shares) and yes_px > 0.0,
        "NO": edge_ok and (-naked < cfg.max_naked_shares) and no_px > 0.0,
    }
    desired_px = {"YES": yes_px, "NO": no_px}

    for side in ("YES", "NO"):
        ro = resting.get(side)
        if want[side]:
            if ro is None:
                plan.posts.append(Quote(side, desired_px[side], cfg.flat_size))
            elif ro.price != desired_px[side]:
                # stale price → re-quote at the new top of book
                plan.cancels.append(ro.order_id)
                plan.posts.append(Quote(side, desired_px[side], cfg.flat_size))
            # else: resting already at the right price — keep it (no churn)
        else:
            if ro is not None:
                plan.cancels.append(ro.order_id)
    return plan
