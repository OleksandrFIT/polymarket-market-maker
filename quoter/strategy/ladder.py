"""Compute the desired ladder of resting limit-bids for one market.

Pure function: given mid-of-book, time-to-expiry, inventory imbalance, and
config — return the list of quotes we WANT to be in.

Phase-9 Bonereaper-clone strategy:

* **Layer A — continuous coverage**: bids on EVERY cent from the top of
  the cheap-tail up to ``mid - 1¢``. Where Bonereaper has 31-47 quotes
  per market on a continuous price grid, this replicates that pattern.
* **Tight cluster** — first ``tight_cluster_levels`` (default 3) cents
  below mid get a ``2×`` size multiplier. These are first to fill when
  the ask drops by 1c and we want queue priority there.
* **Layer B — cheap-tail**: small lottery-ticket bids at 1c..10c on
  BOTH sides. These fire on a market reversal (e.g. BTC pumps last
  second when we held cheap NO).
* **Directional size skew** — when mid is polarized (not 0.5), size
  winning-side bids LARGER and losing-side SMALLER. Imitates
  Bonereaper's "let imbalance build with the market" pattern.
* **Late-window stack** — in the last ``late_window_sec`` seconds, if
  mid past the dominant threshold, multiply dominant-side sizing by
  ``late_window_size_multiplier`` (3×). This is Bonereaper's observed
  "30s-before-expiry $5K stack" behavior.
* **Inventory skew** — disabled by default in Phase 9 (skew_shares=5000
  effectively no cap) since Bonereaper lets imbalance build naturally.
* **Self-cross prevention** — yes_bid + no_bid never sum to
  ``>= 1 - self_cross_buffer``.

The module has NO awareness of orderbook depth or counterparty.
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
    """Return desired bids. 50-100 quotes per market in Phase-9."""
    if not (0.02 <= mid_yes <= 0.98) or time_to_expiry < 5.0:
        return []

    mid_no = 1.0 - mid_yes
    late_window = time_to_expiry < float(cfg.late_window_sec)

    # Inventory skew (hard cap, default disabled)
    net = inventory_yes_qty - inventory_no_qty
    skip_yes = net > cfg.max_inventory_skew_shares
    skip_no = net < -cfg.max_inventory_skew_shares

    # Directional filter (default disabled in Phase 9)
    if cfg.directional_filter_enabled:
        if mid_yes >= cfg.directional_high_threshold:
            skip_no = True
        elif mid_yes <= cfg.directional_low_threshold:
            skip_yes = True

    # Directional size multipliers — size by mid polarization
    yes_mult, no_mult = _directional_multipliers(cfg, mid_yes, late_window)

    out = _layer_a_continuous(
        cfg, mid_yes, mid_no, yes_mult, no_mult, skip_yes, skip_no,
    )
    out.extend(_layer_b_cheap_tail(cfg, mid_yes, mid_no, skip_yes, skip_no))
    return _drop_self_crossing(out, cfg.self_cross_buffer)


def _directional_multipliers(
    cfg: Config, mid_yes: float, late_window: bool
) -> tuple[float, float]:
    """Compute (yes_mult, no_mult) based on mid polarization.

    When mid is 0.5: both 1.0 (neutral).
    When mid > 0.5: yes_mult > 1, no_mult < 1 (winning side bigger).
    Late-window adds extra punch on dominant side past threshold.
    """
    yes_mult = no_mult = 1.0
    if cfg.directional_size_skew_enabled:
        skew = abs(mid_yes - 0.5) * cfg.directional_skew_coef
        if mid_yes > 0.5:
            yes_mult = 1.0 + skew
            no_mult = 1.0 / max(yes_mult, 0.1)
        elif mid_yes < 0.5:
            no_mult = 1.0 + skew
            yes_mult = 1.0 / max(no_mult, 0.1)

    # Late-window aggressive stack on dominant side past threshold
    if late_window:
        thr = cfg.late_window_dominant_threshold
        mult = cfg.late_window_size_multiplier
        if mid_yes >= thr:
            yes_mult *= mult
        elif mid_yes <= 1.0 - thr:
            no_mult *= mult
    return yes_mult, no_mult


def _layer_a_continuous(
    cfg: Config,
    mid_yes: float,
    mid_no: float,
    yes_mult: float,
    no_mult: float,
    skip_yes: bool,
    skip_no: bool,
) -> list[Quote]:
    """Continuous-coverage layer: bid on every cent from the cheap-tail
    boundary up to ``mid - 1¢`` on each side."""
    out: list[Quote] = []

    # Determine where Layer-A starts (above cheap_tail_max so we don't
    # double-quote). Use the max of cheap_tail_levels + 1c.
    tail_max = max(cfg.cheap_tail_levels)
    layer_a_min = round(tail_max + 0.01, 2)

    for offset_idx in range(1, cfg.ladder_levels + 1):
        offset = offset_idx * 0.01

        # YES side
        if not skip_yes:
            p_yes = round(mid_yes - offset, 2)
            if p_yes >= layer_a_min:
                sz = _size_for(cfg, p_yes, offset_idx, "YES", yes_mult)
                if sz >= 5:
                    out.append(Quote("YES", p_yes, sz))

        # NO side
        if not skip_no:
            p_no = round(mid_no - offset, 2)
            if p_no >= layer_a_min:
                sz = _size_for(cfg, p_no, offset_idx, "NO", no_mult)
                if sz >= 5:
                    out.append(Quote("NO", p_no, sz))

    return out


def _layer_b_cheap_tail(
    cfg: Config,
    mid_yes: float,
    mid_no: float,
    skip_yes: bool,
    skip_no: bool,
) -> list[Quote]:
    """Cheap-tail bids on both sides. Each ~$2 risk per quote."""
    out: list[Quote] = []
    for tail_p in cfg.cheap_tail_levels:
        sz = max(5, int(2.0 / max(tail_p, 0.01)))
        if not skip_yes and tail_p < mid_yes - 0.05:
            out.append(Quote("YES", tail_p, sz))
        if not skip_no and tail_p < mid_no - 0.05:
            out.append(Quote("NO", tail_p, sz))
    return out


def _size_for(
    cfg: Config,
    price: float,
    offset_idx: int,
    side: Side,
    directional_mult: float,
) -> int:
    """Size shares for one Layer-A quote.

    Base = budget / 2*levels / price (gives ~$1 per quote at mid prices).
    Tight cluster (first N cents from mid) gets ``tight_cluster_multiplier``.
    Then ``directional_mult`` (1.0 neutral, 1.5-3x for winning side).
    """
    base_usd = cfg.budget_per_market_usd / max(cfg.ladder_levels * 2, 1)
    base_shares = max(cfg.quote_base_size, int(base_usd / max(price, 0.05)))

    # Tight cluster bonus: levels 1..N get extra weight
    cluster_mult = 1.0
    if offset_idx <= cfg.tight_cluster_levels:
        cluster_mult = cfg.tight_cluster_multiplier

    return max(5, int(base_shares * cluster_mult * directional_mult))


def _drop_self_crossing(quotes: list[Quote], buffer: float) -> list[Quote]:
    """Remove (YES, p_y) + (NO, p_n) combos where p_y + p_n >= 1 - buffer."""
    yes_quotes = sorted(
        [q for q in quotes if q.side == "YES"], key=lambda q: -q.price
    )
    no_quotes = sorted(
        [q for q in quotes if q.side == "NO"], key=lambda q: -q.price
    )

    threshold = 1.0 - buffer
    max_no = no_quotes[0].price if no_quotes else 0.0
    yes_keep = [q for q in yes_quotes if q.price + max_no <= threshold]
    max_yes_kept = yes_keep[0].price if yes_keep else 0.0
    no_keep = [q for q in no_quotes if q.price + max_yes_kept <= threshold]
    while yes_keep and no_keep and yes_keep[0].price + no_keep[0].price > threshold:
        yes_keep = yes_keep[1:]

    return yes_keep + no_keep
