"""Phase-16/17 strategy: commit-to-one-side favorite engine + cheap-tail lottery leg.

We follow the Polymarket price. Late in the window (last 40%) the mid has
converged toward the actual outcome, so we BUY the near-certain favorite
(price >= favorite_min_price), committing to one side only for the window.
Size is flat (cfg.flat_size shares per tick — no certainty ramp).
Never adding to a falling side (anti-knife). Buy-only; positions held to resolution.

Phase-17 adds a small cheap-tail lottery leg on the underdog side (competitor
parity): a few bids at low prices, exempt from the favorite gates, bounded by
its own small lottery_cap_usd budget.

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


def _pick_favorite_side(
    mid_yes: float, velocity_short: float | None, cfg: Config,
) -> Side | None:
    """Favorite = the side priced > 0.5. Require Binance velocity to agree if given.

    velocity_short None (e.g. backtest) → skip the confirmation (mid-only).
    """
    side: Side = "YES" if mid_yes > 0.5 else "NO"
    if velocity_short is not None:
        thr = cfg.velocity_confirm_threshold
        if side == "YES" and velocity_short < thr:
            return None
        if side == "NO" and velocity_short > -thr:
            return None
    return side


def _certainty(price: float, window_frac: float, cfg: Config) -> float:
    """Score in [0, 1] rising with BOTH favorite price and window progress.

    Product form: certainty is high only when the price is firm AND the window
    is late — mirroring the competitor's dollar curve (small early, big late).
    """
    pc = (price - cfg.favorite_min_price) / max(
        cfg.max_entry_price - cfg.favorite_min_price, 1e-9,
    )
    tc = (window_frac - cfg.entry_start_frac) / max(1.0 - cfg.entry_start_frac, 1e-9)
    pc = min(1.0, max(0.0, pc))
    tc = min(1.0, max(0.0, tc))
    return pc * tc


def _favorite_ladder(
    side: Side, fav_price: float, size: int, cfg: Config,
) -> list[Quote]:
    """`favorite_ladder_levels` bids descending by 1c from fav_price.

    Top bid sits AT the favorite price so it actually fills as the favorite
    firms; lower bids catch small dips. All capped at max_entry_price.
    """
    top = min(round(fav_price, 2), cfg.max_entry_price)
    out: list[Quote] = []
    for i in range(cfg.favorite_ladder_levels):
        p = round(top - i * 0.01, 2)
        if p <= 0.0:
            continue
        out.append(Quote(side, p, size))
    return out


def _favorite_leg(
    cfg: Config,
    mid_yes: float,
    time_to_expiry: float,
    prev_mid_yes: float | None,
    inventory_yes_qty: int,
    inventory_no_qty: int,
    timeframe: str,
    window_length_sec: float | None,
    velocity_short: float | None,
) -> list[Quote]:
    """Phase-16 favorite engine (unchanged except commit uses majority holding)."""
    if window_length_sec is None:
        window_length_sec = 900.0 if timeframe == "15m" else 300.0
    time_into_window = max(0.0, window_length_sec - time_to_expiry)
    window_frac = min(1.0, time_into_window / window_length_sec)
    if window_frac < cfg.entry_start_frac:
        return []

    side = _pick_favorite_side(mid_yes, velocity_short, cfg)
    if side is None:
        return []

    # Commit-to-one-side: a side counts as the committed FAVORITE only if its $ value
    # exceeds the MOST the lottery alone could ever hold on a side — its cap plus one
    # tick of pre-add overshoot. Below that, the holding might be pure lottery, so it
    # must NOT lock the favorite (prevents the lottery from deadlocking the favorite).
    # Still blocks a genuine favorite (which buys flat_size at >= favorite_min_price,
    # i.e. >= ~$8/tick) from flipping to the other side.
    max_lottery_usd = cfg.lottery_cap_usd + cfg.lottery_size * cfg.lottery_levels * cfg.lottery_max_price
    yes_committed = inventory_yes_qty * mid_yes > max_lottery_usd
    no_committed = inventory_no_qty * (1.0 - mid_yes) > max_lottery_usd
    if yes_committed and side == "NO":
        return []
    if no_committed and side == "YES":
        return []

    fav_price = mid_yes if side == "YES" else (1.0 - mid_yes)
    if fav_price < cfg.favorite_min_price:
        return []

    if prev_mid_yes is not None:
        prev_fav = prev_mid_yes if side == "YES" else (1.0 - prev_mid_yes)
        if fav_price < prev_fav - cfg.rise_tolerance_cents:
            return []

    c = _certainty(fav_price, window_frac, cfg)
    cap_usd = cfg.per_market_cap_usd * (1.0 + c * (cfg.certainty_cap_multiplier - 1.0))
    fav_qty = inventory_yes_qty if side == "YES" else inventory_no_qty
    if fav_qty * fav_price >= cap_usd:
        return []

    return _favorite_ladder(side, fav_price, cfg.flat_size, cfg)


def _lottery_leg(
    cfg: Config, mid_yes: float, inventory_yes_qty: int, inventory_no_qty: int,
) -> list[Quote]:
    """Small cheap-tail lottery bids on the underdog side (competitor parity).

    Exempt from the favorite-leg gates (commit-one-side, entry_start_frac,
    velocity, favorite_min_price). Bounded by its own small lottery_cap_usd.
    """
    if cfg.lottery_size <= 0 or cfg.lottery_levels <= 0:
        return []
    side: Side
    if mid_yes >= 0.5:
        side, price = "NO", round(1.0 - mid_yes, 2)
    else:
        side, price = "YES", round(mid_yes, 2)
    if price <= 0.0 or price > cfg.lottery_max_price:
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
    """Favorite engine (one side, committed) + cheap-tail lottery on the underdog."""
    if not (0.02 <= mid_yes <= 0.99) or time_to_expiry < cfg.min_time_to_expiry_sec:
        return []
    favorite = _favorite_leg(
        cfg, mid_yes, time_to_expiry, prev_mid_yes,
        inventory_yes_qty, inventory_no_qty, timeframe, window_length_sec, velocity_short,
    )
    lottery = _lottery_leg(cfg, mid_yes, inventory_yes_qty, inventory_no_qty)
    return favorite + lottery
