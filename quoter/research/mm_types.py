"""Shared value types for the offline market-maker simulator (quoter/research)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Quote:
    side: str          # "Up" | "Down"
    price: float       # our resting BID price (we only ever buy — never sell)
    size: float        # shares


@dataclass(frozen=True)
class Theta:
    """Calibrated fill params. `fill` = fraction of crossing taker volume we capture
    (encodes queue depth ahead of us). `lag` = seconds a quote stays exposed to the tape
    after a refresh tick (cancel latency; applied by the simulator when slicing the tape)."""
    fill: float
    lag: float = 0.0


@dataclass
class FillResult:
    filled: float
    avg_price: float   # a maker bid fills at its OWN price, so this equals the quote price


@dataclass
class WindowResult:
    gross_up: float    # total shares filled on Up over the window (before merge removal)
    gross_dn: float
    avg_up: float      # cost-weighted avg fill price, Up
    avg_dn: float
    pair_cost: float   # avg_up + avg_dn (matched-pair cost; <1 => locked spread on merge)
    spent: float       # total USDC out
    returned: float    # merge redemptions + winner redemptions at resolution
    pnl: float         # returned - spent
    adverse: float     # loser shares held to resolution (lost their cost)
