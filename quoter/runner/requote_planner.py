"""Pure re-quoting brain — decides what to cancel/post each tick. No I/O.

Fixes from the first live validation (the −$0.40 window):
  * BUG 1 — edge gate now uses the price we ALREADY PAID on a held leg, so we
    never complete a pair for >= $1 (the loss came from buying the 2nd leg at the
    current book price while ignoring what the 1st leg cost).
  * BUG 2 — a per-side target caps how much we buy on each side (no over-buying
    the cheap side into an imbalance).
  * BUG 3 — posts are clamped strictly below the best ask so a post_only order can
    never cross the book (the "invalid post-only order" rejections).

Fully unit- and simulation-testable without any network or live trading.
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
    cancels: list[str] = field(default_factory=list)
    posts: list[Quote] = field(default_factory=list)


def _desired_price(bid: float, ask: float | None) -> float:
    """Top-of-book join price, clamped strictly below the ask (never crosses)."""
    p = round(bid, 2)
    if ask and ask > 0:
        p = min(p, round(ask - 0.01, 2))
    return p


def plan_requote(
    *,
    yes_bid: float,
    no_bid: float,
    yes_ask: float | None = None,
    no_ask: float | None = None,
    inv_yes: int,
    inv_no: int,
    yes_cost: float,
    no_cost: float,
    committed: float,
    target_shares: int,
    resting: dict[str, RestingOrder | None],
    cfg: Config,
) -> RequotePlan:
    """Return the (cancels, posts) plan for this tick. Pure + deterministic."""
    plan = RequotePlan()

    # Gate 1 — CAPITAL: total committed (filled + resting) hit the budget → pull.
    if committed >= cfg.per_market_cap_usd:
        for ro in resting.values():
            if ro is not None:
                plan.cancels.append(ro.order_id)
        return plan

    yes_px = _desired_price(yes_bid, yes_ask)   # BUG 3: below ask
    no_px = _desired_price(no_bid, no_ask)
    yes_avg = (yes_cost / inv_yes) if inv_yes > 0 else None
    no_avg = (no_cost / inv_no) if inv_no > 0 else None
    naked = inv_yes - inv_no

    def pair_ok(side: Side) -> bool:
        """BUG 1: the pair this post would form must cost < $1, using the price we
        ALREADY PAID on the leg we hold (not the current book) when completing."""
        if side == "YES":
            other = no_avg if (naked < 0 and no_avg is not None) else no_px
            return (yes_px + other) < 1.0
        other = yes_avg if (naked > 0 and yes_avg is not None) else yes_px
        return (no_px + other) < 1.0

    # BUG 2: per-side target caps accumulation; plus the naked cap.
    want = {
        "YES": yes_px > 0.0 and inv_yes < target_shares
        and (naked < cfg.max_naked_shares) and pair_ok("YES"),
        "NO": no_px > 0.0 and inv_no < target_shares
        and (-naked < cfg.max_naked_shares) and pair_ok("NO"),
    }
    desired_px = {"YES": yes_px, "NO": no_px}

    for side in ("YES", "NO"):
        ro = resting.get(side)
        if want[side]:
            if ro is None:
                plan.posts.append(Quote(side, desired_px[side], cfg.flat_size))
            elif ro.price != desired_px[side]:
                plan.cancels.append(ro.order_id)
                plan.posts.append(Quote(side, desired_px[side], cfg.flat_size))
            # else keep (no churn)
        else:
            if ro is not None:
                plan.cancels.append(ro.order_id)
    return plan
