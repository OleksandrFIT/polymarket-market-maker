"""Compute the desired ladder of resting limit-bids for one market.

Pure function: given mid-of-book, time-to-expiry, inventory imbalance, and
config — return the list of quotes we WANT to be in.

Phase-11 Bonereaper-confirmed strategy:

* **Layer A — continuous coverage** (Phase 9): bids on every cent from the
  top of cheap-tail up to ``mid - 1¢``.
* **Tight cluster** (Phase 9): top 3 cents below mid get 2× sizing for
  queue priority on the most-likely-to-fill levels.
* **Layer B — cheap-tail** (Phase 9 + 11): bids at 1c..10c. When mid is
  polarized (> 0.75 or < 0.25), the CHEAP side gets 60% of budget
  (matching Bonereaper's observed 64% allocation to cheap side).
* **Timing curve** (Phase 11, replaces late_window_stack): per-timeframe
  size multiplier following Bonereaper's observed FRONT-LOADED pattern.
  Peak aggression in first 10% of window, taper to ~0.1× by end.
* **Conviction sizing** (Phase 11): default budget is small ($25). When
  conviction triggers fire (early on 15m, OR BTC at extreme mid), budget
  multiplied by ``conviction_budget_multiplier`` (6× → ~$150).
* **Directional size skew** (Phase 9): winning side gets larger size,
  losing side smaller. Subdued in Phase 11.
* **Inventory skew**: hard cap effectively disabled (skew_shares=5000)
  since Bonereaper lets imbalance build naturally.
* **Self-cross prevention**: yes_bid + no_bid kept below 1 - buffer.
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


def compute_ladder(  # noqa: C901  (orchestration of many strategy layers)
    cfg: Config,
    mid_yes: float,
    time_to_expiry: float,
    *,
    committed_side: Side | None = None,
    inventory_yes_qty: int = 0,
    inventory_no_qty: int = 0,
    timeframe: str = "5m",
    asset: str | None = None,
    window_length_sec: float | None = None,
) -> list[Quote]:
    """Return desired bids.

    Args:
        timeframe: "5m" or "15m" — picks which timing curve.
        asset: "BTC" / "ETH" / etc — for conviction triggers.
        window_length_sec: if None, derived from timeframe (300 / 900).
    """
    if not (0.02 <= mid_yes <= 0.98) or time_to_expiry < 5.0:
        return []

    mid_no = 1.0 - mid_yes
    if window_length_sec is None:
        window_length_sec = 900.0 if timeframe == "15m" else 300.0
    time_into_window = max(0.0, window_length_sec - time_to_expiry)
    window_frac = min(1.0, time_into_window / window_length_sec)

    # ── Phase-11: TIMING multiplier from front-loaded curve ──
    timing_mult = _timing_multiplier(cfg, timeframe, window_frac)

    # ── Phase-11: budget after conviction check ──
    is_conviction = _is_conviction_play(
        cfg, mid_yes, time_into_window, timeframe, asset,
    )
    budget = cfg.budget_per_market_usd * (
        cfg.conviction_budget_multiplier if is_conviction else 1.0
    )

    # Inventory skew (hard cap, default disabled in Phase 9)
    net = inventory_yes_qty - inventory_no_qty
    skip_yes = net > cfg.max_inventory_skew_shares
    skip_no = net < -cfg.max_inventory_skew_shares

    # Directional filter (default OFF since Phase 9)
    if cfg.directional_filter_enabled:
        if mid_yes >= cfg.directional_high_threshold:
            skip_no = True
        elif mid_yes <= cfg.directional_low_threshold:
            skip_yes = True

    # Directional size multipliers
    yes_mult, no_mult = _directional_multipliers(cfg, mid_yes)
    # Apply timing globally
    yes_mult *= timing_mult
    no_mult *= timing_mult

    out = _layer_a_continuous(
        cfg, mid_yes, mid_no, yes_mult, no_mult, skip_yes, skip_no, budget,
    )
    out.extend(_layer_b_cheap_tail(
        cfg, mid_yes, mid_no, skip_yes, skip_no, budget, timing_mult,
    ))
    return _drop_self_crossing(out, cfg.self_cross_buffer)


def _timing_multiplier(cfg: Config, timeframe: str, window_frac: float) -> float:
    """Pick the size multiplier from the per-timeframe timing curve."""
    curve = cfg.timing_curve_15m if timeframe == "15m" else cfg.timing_curve_5m
    for frac_start, frac_end, mult in curve:
        if frac_start <= window_frac < frac_end:
            return float(mult)
    # Edge: window_frac == 1.0 — use last bucket
    return float(curve[-1][2]) if curve else 1.0


def _is_conviction_play(
    cfg: Config,
    mid_yes: float,
    time_into_window: float,
    timeframe: str,
    asset: str | None,
) -> bool:
    """Phase-11 conviction triggers. ANY-of:
       1. timeframe=15m AND first 30s of window (Bonereaper's "early big" pattern)
       2. asset in conviction_assets AND mid extreme (|mid-0.5| > threshold)
    """
    # Trigger 1: early 15m
    if (
        timeframe == "15m"
        and time_into_window <= cfg.conviction_window_open_max_sec
    ):
        return True
    # Trigger 2: BTC + extreme mid
    if asset and asset.upper() in (a.upper() for a in cfg.conviction_assets):
        if abs(mid_yes - 0.5) > cfg.conviction_extreme_mid_threshold:
            return True
    return False


def _directional_multipliers(
    cfg: Config, mid_yes: float
) -> tuple[float, float]:
    """Winning side bigger, losing side smaller (Phase-9 skew).

    Late-window stack from Phase 9 is NEUTRALIZED in Phase 11
    (late_window_size_multiplier=1.0 by default).
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
    return yes_mult, no_mult


def _layer_a_continuous(
    cfg: Config,
    mid_yes: float,
    mid_no: float,
    yes_mult: float,
    no_mult: float,
    skip_yes: bool,
    skip_no: bool,
    budget: float,
) -> list[Quote]:
    """Continuous bids from cheap-tail-max+1c up to mid-1c each side."""
    out: list[Quote] = []
    tail_max = max(cfg.cheap_tail_levels)
    layer_a_min = round(tail_max + 0.01, 2)

    for offset_idx in range(1, cfg.ladder_levels + 1):
        offset = offset_idx * 0.01
        if not skip_yes:
            p_yes = round(mid_yes - offset, 2)
            if p_yes >= layer_a_min:
                sz = _size_for(cfg, p_yes, offset_idx, yes_mult, budget)
                if sz >= 5:
                    out.append(Quote("YES", p_yes, sz))
        if not skip_no:
            p_no = round(mid_no - offset, 2)
            if p_no >= layer_a_min:
                sz = _size_for(cfg, p_no, offset_idx, no_mult, budget)
                if sz >= 5:
                    out.append(Quote("NO", p_no, sz))
    return out


def _layer_b_cheap_tail(
    cfg: Config,
    mid_yes: float,
    mid_no: float,
    skip_yes: bool,
    skip_no: bool,
    budget: float,
    timing_mult: float,
) -> list[Quote]:
    """Cheap-tail bids. When polarized: 60% of budget to cheap side.

    Normally each cheap-tail quote ~$2 risk. When market is polarized,
    we BIAS sizing toward the CHEAP side — matching Bonereaper's 64%
    allocation when mid is skewed.
    """
    out: list[Quote] = []
    is_polarized = mid_yes >= cfg.polarized_threshold or mid_yes <= (1.0 - cfg.polarized_threshold)
    cheap_pct = cfg.polarized_cheap_side_pct

    # Determine which side is "cheap" (the losing-implied side)
    cheap_side = None
    if is_polarized:
        cheap_side = "NO" if mid_yes >= cfg.polarized_threshold else "YES"

    # Budget per cheap-tail bid (in USDC risk)
    base_risk_usd = 2.0 * timing_mult  # follows timing curve too
    cheap_boost = max(1.0, cheap_pct / 0.5 * 2)  # ~2.4× when polarized

    for tail_p in cfg.cheap_tail_levels:
        # YES cheap-tail
        if not skip_yes and tail_p < mid_yes - 0.05:
            mult = cheap_boost if cheap_side == "YES" else 1.0
            sz = max(5, int((base_risk_usd * mult) / max(tail_p, 0.01)))
            out.append(Quote("YES", tail_p, sz))
        # NO cheap-tail
        if not skip_no and tail_p < mid_no - 0.05:
            mult = cheap_boost if cheap_side == "NO" else 1.0
            sz = max(5, int((base_risk_usd * mult) / max(tail_p, 0.01)))
            out.append(Quote("NO", tail_p, sz))
    return out


def _size_for(
    cfg: Config,
    price: float,
    offset_idx: int,
    directional_mult: float,
    budget: float,
) -> int:
    """Layer-A quote size.

    Base = budget / 2*levels / price (gives ~$1 per quote at mid prices).
    Tight cluster (top N) gets ``tight_cluster_multiplier``.
    Multiply by directional_mult (includes timing curve).
    """
    base_usd = budget / max(cfg.ladder_levels * 2, 1)
    base_shares = max(cfg.quote_base_size, int(base_usd / max(price, 0.05)))
    cluster_mult = 1.0
    if offset_idx <= cfg.tight_cluster_levels:
        cluster_mult = cfg.tight_cluster_multiplier
    return max(5, int(base_shares * cluster_mult * directional_mult))


def _drop_self_crossing(quotes: list[Quote], buffer: float) -> list[Quote]:
    """Drop YES+NO pairs whose price sum >= 1 - buffer (wash-trade guard)."""
    yes_quotes = sorted([q for q in quotes if q.side == "YES"], key=lambda q: -q.price)
    no_quotes = sorted([q for q in quotes if q.side == "NO"], key=lambda q: -q.price)
    threshold = 1.0 - buffer
    max_no = no_quotes[0].price if no_quotes else 0.0
    yes_keep = [q for q in yes_quotes if q.price + max_no <= threshold]
    max_yes_kept = yes_keep[0].price if yes_keep else 0.0
    no_keep = [q for q in no_quotes if q.price + max_yes_kept <= threshold]
    while yes_keep and no_keep and yes_keep[0].price + no_keep[0].price > threshold:
        yes_keep = yes_keep[1:]
    return yes_keep + no_keep
