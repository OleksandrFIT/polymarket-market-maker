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

    # ── Phase-19 two-sided merge-maker ──
    # Post maker bids on BOTH outcomes at a target pair cost < $1.00
    # (Up @ mid-δ, Down @ (1-mid)-δ, where δ = merge_edge/2); the matched
    # complementary pairs are merged to $1.00, locking the spread regardless of
    # direction. A balance gate caps naked (one-sided) exposure. Buy-only.
    merge_edge: float = 0.01             # target total edge per Up+Down pair (per-leg δ = /2)
    max_naked_shares: int = 20           # hard cap on |yes_qty - no_qty|
    merge_levels: int = 2                # bids per side per tick
    flat_size: int = 10                  # flat shares per bid
    per_market_cap_usd: float = 50.0     # $ ceiling on ACCUMULATED spend per market
    min_time_to_expiry_sec: float = 5.0  # below this → no quotes

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
