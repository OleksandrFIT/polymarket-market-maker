"""Compute the desired ladder of resting limit-bids for one market.

Pure function: given mid-of-book, time-to-expiry, inventory imbalance, and
config — return the list of quotes we WANT to be in. ``OrderManager`` later
diffs this against currently-live orders to decide cancel/post.

Strategy (Bonereaper-style market-making):

* Layer A — tight bids: ``ladder_levels`` levels 1¢..N¢ BELOW mid on each
  side. Sizing increases with depth (deeper offsets get more shares).
* Layer B — cheap-tail bids: small fixed bets at 1¢, 2¢, 3¢, 5¢ on BOTH
  sides. Covers tail scenarios (market flips at expiry).
* Inventory skew: if heavily long one side, skip that side entirely so
  fills rebalance us. Affects BOTH Layer-A and Layer-B.
* Directional filter: when mid_yes is past polarization threshold, suppress
  Layer-A on the losing side (adverse-selection trap). Layer-B cheap-tail
  remains on BOTH sides — those bids are positive-EV regardless because
  they only fire on a market reversal.
* Late-window committed-side bias: in last 60s, scale up sizing on the
  committed side, scale down opposite.
* Self-cross prevention: yes_bid + no_bid kept below ``1 - self_cross_buffer``.

This module has NO awareness of orderbook depth or counterparty. It just
emits desired state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from quoter.config import Config

Side = Literal["YES", "NO"]


@dataclass(frozen=True, slots=True)
class Quote:
    """One desired resting limit-bid order."""

    side: Side
    price: float  # 0.01 .. 0.99 (cents)
    size: int  # shares; Polymarket min 5


def compute_ladder(
    cfg: Config,
    mid_yes: float,
    time_to_expiry: float,
    *,
    committed_side: Side | None = None,
    inventory_yes_qty: int = 0,
    inventory_no_qty: int = 0,
) -> list[Quote]:
    """Return desired bids for one market. Order-of-magnitude 20-50 quotes."""
    if not (0.02 <= mid_yes <= 0.98) or time_to_expiry < 5.0:
        return []

    mid_no = 1.0 - mid_yes
    late_window = time_to_expiry < 60.0

    # Inventory skew: skip side that's already heavily long
    net = inventory_yes_qty - inventory_no_qty
    skip_yes_layer_a = net > cfg.max_inventory_skew_shares
    skip_no_layer_a = net < -cfg.max_inventory_skew_shares
    skip_yes_tail = skip_yes_layer_a
    skip_no_tail = skip_no_layer_a

    # Directional filter: market polarized → don't quote Layer-A on losing
    # side (adverse selection trap). Keep cheap-tail on both sides — those
    # are positive-EV lottery tickets regardless of direction.
    if cfg.directional_filter_enabled:
        if mid_yes >= cfg.directional_high_threshold:
            skip_no_layer_a = True  # NO is the losing side
        elif mid_yes <= cfg.directional_low_threshold:
            skip_yes_layer_a = True  # YES is the losing side

    out = _layer_a(
        cfg, mid_yes, mid_no, committed_side, late_window,
        skip_yes_layer_a, skip_no_layer_a,
    )
    out.extend(_layer_b_cheap_tail(cfg, mid_yes, mid_no, skip_yes_tail, skip_no_tail))
    return _drop_self_crossing(out, cfg.self_cross_buffer)


def _layer_a(
    cfg: Config,
    mid_yes: float,
    mid_no: float,
    committed: Side | None,
    late: bool,
    skip_yes: bool,
    skip_no: bool,
) -> list[Quote]:
    """Tight bids 1¢..N¢ below mid on each side."""
    out: list[Quote] = []
    for offset_idx in range(1, cfg.ladder_levels + 1):
        offset = offset_idx * 0.01
        if not skip_yes:
            p = round(mid_yes - offset, 2)
            if p > 0.01:
                sz = _size_for(cfg, p, offset_idx, "YES", committed, late)
                if sz >= 5:
                    out.append(Quote("YES", p, sz))
        if not skip_no:
            p = round(mid_no - offset, 2)
            if p > 0.01:
                sz = _size_for(cfg, p, offset_idx, "NO", committed, late)
                if sz >= 5:
                    out.append(Quote("NO", p, sz))
    return out


def _layer_b_cheap_tail(
    cfg: Config,
    mid_yes: float,
    mid_no: float,
    skip_yes: bool,
    skip_no: bool,
) -> list[Quote]:
    """Cheap-tail bids at 1¢..5¢ on both sides. ~$2 risk per quote."""
    out: list[Quote] = []
    for tail_p in cfg.cheap_tail_levels:
        sz = max(5, int(2.0 / max(tail_p, 0.01)))
        if not skip_yes and tail_p < mid_yes - 0.10:
            out.append(Quote("YES", tail_p, sz))
        if not skip_no and tail_p < mid_no - 0.10:
            out.append(Quote("NO", tail_p, sz))
    return out


def _size_for(
    cfg: Config,
    price: float,
    offset_idx: int,
    side: Side,
    committed: Side | None,
    late: bool,
) -> int:
    """Size shares for one quote.

    Heuristic: base USDC / price → base shares; deeper offsets get a
    modest bonus; late-window committed-side gets 50% larger, opposite
    50% smaller.
    """
    # Allocate ~$1 per Layer-A level if budget_per_market_usd=25 and 25 levels
    base_usd_per_quote = cfg.budget_per_market_usd / max(cfg.ladder_levels * 2, 1)
    base_shares = max(cfg.quote_base_size, int(base_usd_per_quote / max(price, 0.05)))
    depth_mult = 1.0 + offset_idx * 0.10
    skew_mult = 1.0
    if late and committed is not None:
        skew_mult = 1.5 if side == committed else 0.5
    return int(base_shares * depth_mult * skew_mult)


def _drop_self_crossing(quotes: list[Quote], buffer: float) -> list[Quote]:
    """Remove (YES, p_y) + (NO, p_n) combos where p_y + p_n >= 1 - buffer.

    Strategy: for each YES quote, find the highest NO quote that crosses
    and drop the SHALLOWER one (closer to mid) since deeper levels are
    cheaper inventory.
    """
    yes_quotes = sorted([q for q in quotes if q.side == "YES"], key=lambda q: -q.price)
    no_quotes = sorted([q for q in quotes if q.side == "NO"], key=lambda q: -q.price)

    # Find largest YES price and largest NO price; if they sum too high,
    # drop the most expensive (closest to mid) on whichever side has more.
    # Simpler: per-quote check — if any YES.price + any NO.price >= 1-buf,
    # we need to skip at least one. Easiest: cap each side's max price.
    threshold = 1.0 - buffer
    max_no = no_quotes[0].price if no_quotes else 0.0
    yes_keep = [q for q in yes_quotes if q.price + max_no <= threshold]
    max_yes_kept = yes_keep[0].price if yes_keep else 0.0
    no_keep = [q for q in no_quotes if q.price + max_yes_kept <= threshold]
    # If we just dropped all NO quotes due to too-high YES, retry: drop top YES
    # to relax max_yes
    while yes_keep and no_keep and yes_keep[0].price + no_keep[0].price > threshold:
        yes_keep = yes_keep[1:]

    return yes_keep + no_keep
