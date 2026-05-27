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

    # ── Ladder parameters ──
    ladder_levels: int = 12  # offset 1c .. 12c below mid each side
    cheap_tail_levels: tuple[float, ...] = (0.01, 0.02, 0.03, 0.05)
    budget_per_market_usd: float = 25.0
    quote_base_size: int = 10  # shares (Polymarket min 5)
    self_cross_buffer: float = 0.01  # don't post yes_bid + no_bid >= 1.0 - this

    # ── Quoter loop ──
    requote_min_interval_ms: int = 100  # max 10×/sec per market
    requote_on_mid_move_cents: int = 1  # threshold to mark market dirty

    # ── Risk ──
    max_daily_loss_usd: float = 50.0
    max_inventory_skew_shares: int = 500
    max_market_position_usd: float = 200.0
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
