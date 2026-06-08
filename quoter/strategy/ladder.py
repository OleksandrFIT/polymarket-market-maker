"""Phase-18 strategy: velocity-driven momentum entry (cheap side) + cheap-tail lottery leg.

Pure function: compute_ladder(cfg, mid_yes, time_to_expiry, ...) -> list[Quote].
All state (the previous mid, inventory) is passed in by the caller.
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


def _favorite_ladder(
    side: Side, fav_price: float, size: int, cfg: Config,
) -> list[Quote]:
    """`favorite_ladder_levels` bids descending by 1c from the entry price.

    Top bid sits AT the entry price so it fills as the side firms; lower bids
    catch small dips. All capped at max_entry_price (inherited bound; under the
    momentum band [<=0.65] it never binds).
    """
    top = min(round(fav_price, 2), cfg.max_entry_price)
    out: list[Quote] = []
    for i in range(cfg.favorite_ladder_levels):
        p = round(top - i * 0.01, 2)
        if p <= 0.0:
            continue
        out.append(Quote(side, p, size))
    return out


def _momentum_leg(
    cfg: Config,
    mid_yes: float,
    velocity_short: float | None,
    inventory_yes_qty: int,
    inventory_no_qty: int,
) -> list[Quote]:
    """Buy the side Binance momentum favors WHILE STILL CHEAP (cost-basis fix).

    Side comes from the velocity sign (not the current favorite); we only buy in
    the cheap band [momentum_min_price, momentum_max_price] so the average cost
    basis stays low. No signal (incl. backtest velocity=None) → no bids.
    """
    if velocity_short is None or abs(velocity_short) < cfg.momentum_velocity_threshold:
        return []
    side: Side = "YES" if velocity_short > 0 else "NO"
    price = round(mid_yes, 2) if side == "YES" else round(1.0 - mid_yes, 2)
    if not (cfg.momentum_min_price <= price <= cfg.momentum_max_price):
        return []

    # Commit-to-one-side ($-value threshold, robust to lottery — same as phase-17).
    max_lottery_usd = cfg.lottery_cap_usd + cfg.lottery_size * cfg.lottery_levels * cfg.lottery_max_price
    yes_committed = inventory_yes_qty * mid_yes > max_lottery_usd
    no_committed = inventory_no_qty * (1.0 - mid_yes) > max_lottery_usd
    if yes_committed and side == "NO":
        return []
    if no_committed and side == "YES":
        return []

    # Per-market cap (flat).
    qty = inventory_yes_qty if side == "YES" else inventory_no_qty
    if qty * price >= cfg.per_market_cap_usd:
        return []

    return _favorite_ladder(side, price, cfg.flat_size, cfg)


def _lottery_leg(
    cfg: Config, mid_yes: float, inventory_yes_qty: int, inventory_no_qty: int,
) -> list[Quote]:
    """Small cheap-tail lottery bids on the underdog side (competitor parity).

    Exempt from the momentum-leg gates (commit-one-side, velocity, price band).
    Bounded by its own small lottery_cap_usd; fires only for underdog price
    strictly below lottery_max_price (the momentum band starts there).
    """
    if cfg.lottery_size <= 0 or cfg.lottery_levels <= 0:
        return []
    side: Side
    if mid_yes >= 0.5:
        side, price = "NO", round(1.0 - mid_yes, 2)
    else:
        side, price = "YES", round(mid_yes, 2)
    if price <= 0.0 or price >= cfg.lottery_max_price:
        return []
    udog_qty = inventory_yes_qty if side == "YES" else inventory_no_qty
    if udog_qty * price >= cfg.lottery_cap_usd:
        return []
    out: list[Quote] = []
    for i in range(cfg.lottery_levels):
        p = round(price - i * 0.01, 2)
        if p <= 0.0:
            continue
        out.append(Quote(side, p, cfg.lottery_size))
    return out


def compute_ladder(
    cfg: Config,
    mid_yes: float,
    time_to_expiry: float,
    *,
    prev_mid_yes: float | None = None,
    inventory_yes_qty: int = 0,
    inventory_no_qty: int = 0,
    timeframe: str = "5m",
    asset: str | None = None,
    window_length_sec: float | None = None,
    velocity_short: float | None = None,
    # committed_side and velocity_long: legacy kwargs accepted for caller compat; ignored.
    committed_side: Side | None = None,
    velocity_long: float | None = None,
) -> list[Quote]:
    """Momentum entry (velocity-favored side, bought cheap) + cheap-tail lottery."""
    if not (0.02 <= mid_yes <= 0.99) or time_to_expiry < cfg.min_time_to_expiry_sec:
        return []
    momentum = _momentum_leg(
        cfg, mid_yes, velocity_short, inventory_yes_qty, inventory_no_qty,
    )
    lottery = _lottery_leg(cfg, mid_yes, inventory_yes_qty, inventory_no_qty)
    return momentum + lottery
