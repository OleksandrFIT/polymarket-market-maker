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
    timeframes: tuple[str, ...] = ("5m",)  # 15m disabled: paper showed -$30 vs +$39 on 5m

    # ── Phase-16 late-window favorite-buying (commit-to-one-side, flat size) ──
    # Strategy follows the Polymarket price: only in the last 40% of the window
    # (entry_start_frac) and only when the near-certain favorite (>= favorite_min_price)
    # is found. We BUY that side with flat_size shares per tick (no certainty ramp),
    # commit to one side for the window (no cross-side adds), and never add to a
    # falling side. Buy-only, held to resolution.
    favorite_min_price: float = 0.85       # phase-16: only near-certain favorites
    max_entry_price: float = 0.97          # phase-16: allow >=0.95 like competitor
    entry_start_frac: float = 0.60         # phase-16: only the last 40% of window
    flat_size: int = 10                    # phase-16: flat shares per tick (no ramp-into-price)
    per_market_cap_usd: float = 50.0       # $ ceiling on ACCUMULATED favorite spend per market
    certainty_cap_multiplier: float = 2.0  # cap scales up to ×this under certainty
    velocity_confirm_threshold: float = 0.0005  # min Binance velocity to confirm side (0.05% per lookback)
    rise_tolerance_cents: float = 0.01     # favorite may dip this much vs prev and still quote
    favorite_ladder_levels: int = 3        # one-sided bids per tick
    min_time_to_expiry_sec: float = 5.0    # below this → no quotes

    # ── Phase-17 cheap-tail lottery leg (competitor parity) ──
    lottery_max_price: float = 0.40    # buy underdog only if its price <= this
    lottery_cap_usd: float = 3.0       # separate small $ budget for the lottery leg
    lottery_size: int = 5              # shares per lottery bid (0 disables)
    lottery_levels: int = 2            # cheap-tail lottery bids per tick (0 disables)

    # ── Phase-18 momentum entry (cost-basis fix: buy velocity-favored side while cheap) ──
    momentum_velocity_threshold: float = 0.001  # min |Binance velocity| to trigger an entry
    momentum_min_price: float = 0.40            # don't buy below (too uncertain)
    momentum_max_price: float = 0.65            # don't buy above (missed cheap entry → -EV)

    # ── Phase-12 Binance velocity signal ──
    velocity_short_lookback_sec: float = 30.0   # for directional skew gating
    velocity_long_lookback_sec: float = 60.0    # for conviction confirmation
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
