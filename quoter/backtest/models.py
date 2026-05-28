"""Data models for the offline backtest harness."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketWindow:
    """One resolved market to replay."""

    market_id: str
    asset: str
    timeframe: str
    open_ts: int
    expire_ts: int
    winning_side: str  # "YES" | "NO"
    yes_token: str

    @property
    def window_length(self) -> int:
        return self.expire_ts - self.open_ts


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A single (timestamp, YES-price) sample from CLOB prices-history."""

    t: int
    yes_price: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """PnL outcome of replaying one market under one config."""

    market_id: str
    pnl: float
    yes_qty: float
    no_qty: float
    total_cost: float
    n_fills: int
