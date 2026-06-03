"""Runtime configuration for poly-quoter.

Loaded once at startup from environment variables (.env file).
Frozen dataclass — no runtime mutation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

Mode = Literal["shadow", "paper", "live"]


@dataclass(frozen=True)
class Config:
    """All runtime settings. Immutable after construction."""

    # ── Execution mode ──
    mode: Mode = "shadow"
    bankroll_usd: float = 100.0

    # ── Markets ──
    assets: tuple[str, ...] = ("BTC", "ETH")
    timeframes: tuple[str, ...] = ("5m", "15m")

    # ── Ladder parameters (Phase-9 Bonereaper-clone) ──
    # CONTINUOUS COVERAGE: a bid on every cent from cheap-tail to mid.
    # ladder_levels: 12 → 50 (covers ~half the price grid)
    # cheap_tail: extended to 0.01-0.10 (he goes deep on tails)
    ladder_levels: int = 50
    cheap_tail_levels: tuple[float, ...] = (
        0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10,
    )
    budget_per_market_usd: float = 50.0   # was 25; need more for 50 levels
    quote_base_size: int = 5              # Polymarket min; let levels add depth
    self_cross_buffer: float = 0.02       # broader gap to prevent self-cross

    # ── Tight cluster near top (Bonereaper-style queue priority bid) ──
    # Cluster of N quotes within K cents of mid with 2× sizing — these are
    # the first to fill when ask drops by 1c.
    tight_cluster_levels: int = 3         # 3 extra bids in mid-1c..mid-3c band
    tight_cluster_multiplier: float = 2.0  # 2× normal size in that band

    # ── Directional filter (Phase-8 — DISABLED in Phase 9 for Bonereaper-style) ──
    # Bonereaper does NOT skip losing side — he lets imbalance build.
    # Instead we SIZE losing side smaller (directional_size_skew below).
    directional_filter_enabled: bool = False  # Phase-14: data showed it loads losing side
    directional_high_threshold: float = 0.70
    directional_low_threshold: float = 0.30

    # ── Directional size skew (Phase-9) ──
    # When mid polarized, size winning-side bids LARGER, losing-side SMALLER.
    # Multiplier formula: 1 + |mid - 0.5| × skew_coef on winning side.
    # At mid=0.5: 1.0× both sides. At mid=0.8: winning 1.6×, losing 0.625×.
    directional_size_skew_enabled: bool = False  # Phase-14: off (amplified losing side)
    directional_skew_coef: float = 2.0

    # ── Phase-15 late-window favorite-buying ──
    # Strategy follows the Polymarket price: late in the window the mid has
    # converged toward the outcome, so we BUY the favorite (the side priced
    # > 0.5), one side only, scaling size with certainty, never adding to a
    # falling side. Buy-only, held to resolution.
    favorite_min_price: float = 0.55       # below this no clear favorite → no quotes
    max_entry_price: float = 0.95          # hard ceiling on any bid (backtest-swept)
    entry_start_frac: float = 0.30         # no entries before this fraction of window
    certainty_size_base: int = 5           # base shares per tick (Polymarket min)
    certainty_size_max: int = 40           # shares per tick at max certainty
    per_market_cap_usd: float = 50.0       # $ ceiling on ACCUMULATED favorite spend per market (distinct from budget_per_market_usd)
    certainty_cap_multiplier: float = 2.0  # cap scales up to ×this under certainty
    velocity_confirm_threshold: float = 0.0005  # min Binance velocity to confirm side (0.05% per lookback)
    rise_tolerance_cents: float = 0.01     # favorite may dip this much vs prev and still quote
    favorite_ladder_levels: int = 3        # one-sided bids per tick
    min_time_to_expiry_sec: float = 5.0    # below this → no quotes

    # ── Legacy phase-14 (DEPRECATED — removed in phase-15 cleanup task) ──
    entry_cutoff_frac: float = 0.50

    # ── Late-window aggressive stack (Phase-9 — DEPRECATED in Phase 11) ──
    # Phase 9 thought Bonereaper does ×3 in last 30s. Data showed OPPOSITE:
    # he backs off in the final minute and front-loads at window open.
    # These knobs are kept for backward compat but neutralized (×1.0).
    late_window_sec: int = 30
    late_window_size_multiplier: float = 1.0   # neutralized
    late_window_dominant_threshold: float = 0.65

    # ── Phase-11 timing curve (Bonereaper-confirmed front-loaded pattern) ──
    # Source: live analysis of 3000 of his trades — 67% of capital deployed
    # in first 4.5 min of 15m window, drops to 1-3% mid-window, modest
    # re-engagement at 10:30-13:30, near-zero in final 90s.
    # Format: tuple of (fraction_start, fraction_end, size_multiplier).
    # ladder uses the bucket matching `(time_into_window/window_length)`.
    timing_curve_5m: tuple[tuple[float, float, float], ...] = (
        (0.00, 0.10, 3.0),   # 0-30s of 5m: ATTACK (window just opened)
        (0.10, 0.30, 2.0),   # 30-90s:      EARLY
        (0.30, 0.60, 1.0),   # 90-180s:     BASE
        (0.60, 0.80, 0.5),   # 180-240s:    TAPER
        (0.80, 1.00, 0.1),   # 240-300s:    MINIMAL (Bonereaper backs off)
    )
    timing_curve_15m: tuple[tuple[float, float, float], ...] = (
        (0.00, 0.10, 3.0),   # 0-90s:       PEAK (27.9% of capital observed)
        (0.10, 0.30, 2.0),   # 90-270s:     EARLY
        (0.30, 0.45, 0.8),   # 270-405s:    DRY SPELL
        (0.45, 0.60, 0.3),   # 405-540s:    VALLEY
        (0.60, 0.85, 1.5),   # 540-765s:    RE-ENGAGEMENT
        (0.85, 1.00, 0.3),   # 765-900s:    TAPER
    )

    # ── Phase-11 polarized cheap-tail dominance ──
    # When market is heavily skewed (mid > 0.75 or < 0.25), Bonereaper puts
    # 64% of capital on the CHEAP side (lottery tickets). Match that.
    polarized_threshold: float = 0.75
    polarized_cheap_side_pct: float = 0.60  # 60% of budget to cheap side

    # ── Phase-11 conviction-based variable sizing ──
    # Default: small probe ($25). Conviction trigger → larger commitment.
    # Triggers (any of):
    #   - timeframe is "15m" AND time_into_window <= 30s (early on 15m)
    #   - asset in conviction_assets (BTC) AND mid extreme (>0.85 or <0.15)
    #     AND binance velocity CONFIRMS mid direction
    conviction_budget_multiplier: float = 6.0  # 6× → ~$150 conviction budget
    conviction_window_open_max_sec: int = 30
    conviction_assets: tuple[str, ...] = ("BTC",)
    conviction_extreme_mid_threshold: float = 0.15  # |mid-0.5| > this

    # ── Phase-12 Binance velocity signal (predictive directional) ──
    # The mid_yes alone is a *coincident* indicator. Binance BTC velocity
    # over a short lookback is a *leading* indicator: if BTC is moving UP
    # in last 30s, YES becomes more likely to be the final winner.
    # We use velocity in two places:
    #   1. directional_skew: skew sizing ONLY if velocity AGREES with mid
    #   2. conviction trigger: require min velocity magnitude on extreme mid
    velocity_short_lookback_sec: float = 30.0   # for directional skew gating
    velocity_long_lookback_sec: float = 60.0    # for conviction confirmation
    # Velocity less than this (abs value) treated as "neutral" — won't gate skew
    velocity_neutral_threshold: float = 0.0005  # 0.05% per lookback
    # Conviction velocity must be at least this much in mid's direction
    conviction_min_velocity: float = 0.001      # 0.1% per 60s
    velocity_buffer_max_age_sec: float = 300.0

    # ── Quoter loop (Phase-9 faster cycle) ──
    requote_min_interval_ms: int = 50   # was 100 → 2× faster cycle
    requote_on_mid_move_cents: int = 1

    # ── Paper-fill realism (Phase-9+) ──
    # The live CLOB has 5-15 maker bids at each price level, plus tier-1
    # makers (Bonereaper) holding queue positions 1-3 with sub-10ms latency
    # from us-east-1. Our laptop sits ~110ms away in Slovakia and starts
    # at the back of the queue. The naive paper model (100% fill on cross)
    # massively over-estimates our edge. Toggle realistic_mode ON to model
    # queue priority + probabilistic taker arrivals + latency penalty.
    paper_fill_realistic_mode: bool = True
    paper_queue_position: int = 8         # default seat (middle of queue)
    paper_taker_size_min: int = 5         # smallest simulated taker SELL
    paper_taker_size_max: int = 200       # largest simulated taker SELL
    paper_latency_ms: int = 110           # round-trip Slovakia → us-east-1
    paper_fill_prob_multiplier: float = 1.0  # global knob (1.0 = baseline)

    # ── Risk ──
    max_daily_loss_usd: float = 50.0
    max_inventory_skew_shares: int = 5000  # effectively disabled (was 500)
    max_market_position_usd: float = 500.0
    stop_after_consecutive_loss_days: int = 2

    # ── WS health ──
    ws_stale_timeout_sec: float = 30.0  # force reconnect if no book update in N sec
    ws_reconnect_backoff_max_sec: float = 30.0

    # ── Paths ──
    db_path: str = "state.db"
    log_path: str = "logs/quoter.log"
    log_level: str = "INFO"

    # ── Polymarket endpoints ──
    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"
    polymarket_web: str = "https://polymarket.com"
    ws_market_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    ws_user_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

    # ── Binance ──
    binance_ws_url: str = "wss://stream.binance.com:9443/stream"

    @classmethod
    def from_env(cls) -> Config:
        """Load from environment. Missing values use dataclass defaults."""
        mode_str = os.environ.get("MODE", "shadow").lower()
        if mode_str not in ("shadow", "paper", "live"):
            raise ValueError(f"MODE must be shadow|paper|live, got {mode_str!r}")
        return cls(
            mode=mode_str,  # type: ignore[arg-type]
            bankroll_usd=float(os.environ.get("BANKROLL", "100")),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )

    @property
    def is_shadow(self) -> bool:
        return self.mode == "shadow"

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"
