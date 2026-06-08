"""Phase-19 strategy: two-sided merge-maker.

Pure function: ``compute_ladder(cfg, mid_yes, time_to_expiry, ...) -> list[Quote]``.
All state (current inventory) is passed in by the caller.

We post maker bids on BOTH outcomes at a target pair cost below $1.00:
``Up @ mid-δ`` and ``Down @ (1-mid)-δ`` where ``δ = merge_edge/2``. The pair then
costs ``1 - merge_edge`` (< $1). The matched complementary pairs are merged back
to $1.00 by the caller (``inventory.on_merge``), locking the maker spread
regardless of which way the market resolves. A balance gate caps naked
(one-sided) exposure so an unfilled leg cannot run into a large directional loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from quoter.config import Config

Side = Literal["YES", "NO"]


@dataclass
class Quote:
    """A single resting BUY bid on one outcome token."""

    side: Side
    price: float  # 0.01 .. 0.99 (cents)
    size: int     # shares; Polymarket min 5


def _ladder(side: Side, top_price: float, size: int, levels: int) -> list[Quote]:
    """`levels` bids descending by 1c from `top_price`, skipping prices <= 0.

    Near the price extremes one leg may yield fewer rungs than the other (lower
    rungs round to <= 0 and are skipped); the balance gate bounds the resulting
    naked drift, so the asymmetry is acceptable.
    """
    out: list[Quote] = []
    for i in range(levels):
        p = round(top_price - i * 0.01, 2)
        if p <= 0.0:
            continue
        out.append(Quote(side, p, size))
    return out


def _merge_ladder(
    cfg: Config,
    mid_yes: float,
    yes_qty: int,
    no_qty: int,
    yes_cost: float,
    no_cost: float,
) -> list[Quote]:
    """Two-sided bids targeting a pair cost < $1.00, gated for edge/capital/balance."""
    delta = cfg.merge_edge / 2.0
    up_price = round(mid_yes - delta, 2)
    dn_price = round((1.0 - mid_yes) - delta, 2)

    # Gate 1 — EDGE: after rounding, the pair must still cost < $1.00.
    if up_price + dn_price >= 1.0:
        return []

    # Gate 2 — CAPITAL: stop once this market's accumulated spend hits its cap.
    if (yes_cost + no_cost) >= cfg.per_market_cap_usd:
        return []

    # Gate 3 — BALANCE: add a leg only if it does not push us further past the
    # naked cap on that side. Suppress the side we are already long of.
    cap = cfg.max_naked_shares
    post_up = (yes_qty - no_qty) < cap
    post_down = (no_qty - yes_qty) < cap

    bids: list[Quote] = []
    if post_up and up_price > 0.0:
        bids += _ladder("YES", up_price, cfg.flat_size, cfg.merge_levels)
    if post_down and dn_price > 0.0:
        bids += _ladder("NO", dn_price, cfg.flat_size, cfg.merge_levels)
    return bids


def compute_ladder(
    cfg: Config,
    mid_yes: float,
    time_to_expiry: float,
    *,
    inventory_yes_qty: int = 0,
    inventory_no_qty: int = 0,
    inventory_yes_cost: float = 0.0,
    inventory_no_cost: float = 0.0,
    **_legacy: object,  # tolerate/ignore any leftover caller kwargs during transition
) -> list[Quote]:
    """Two-sided merge-maker bids (Up + Down at pair cost < $1)."""
    if not (0.02 <= mid_yes <= 0.99) or time_to_expiry < cfg.min_time_to_expiry_sec:
        return []
    return _merge_ladder(
        cfg,
        mid_yes,
        inventory_yes_qty,
        inventory_no_qty,
        inventory_yes_cost,
        inventory_no_cost,
    )
