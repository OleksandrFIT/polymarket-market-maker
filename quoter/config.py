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
    directional_filter_enabled: bool = False
    directional_high_threshold: float = 0.70
    directional_low_threshold: float = 0.30

    # ── Directional size skew (Phase-9) ──
    # When mid polarized, size winning-side bids LARGER, losing-side SMALLER.
    # Multiplier formula: 1 + |mid - 0.5| × skew_coef on winning side.
    # At mid=0.5: 1.0× both sides. At mid=0.8: winning 1.6×, losing 0.625×.
    directional_size_skew_enabled: bool = True
    directional_skew_coef: float = 2.0

    # ── Late-window aggressive stack (Phase-9) ──
    # In last N seconds of a window, post HEAVY bids on dominant side at
    # tight prices (mid-1c, mid-2c). This is Bonereaper's "last-30s loading".
    late_window_sec: int = 30
    late_window_size_multiplier: float = 3.0   # 3× normal size on dominant side
    late_window_dominant_threshold: float = 0.65  # only stack when mid past this

    # ── Quoter loop (Phase-9 faster cycle) ──
    requote_min_interval_ms: int = 50   # was 100 → 2× faster cycle
    requote_on_mid_move_cents: int = 1

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
