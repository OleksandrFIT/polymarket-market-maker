"""Pure pair-completion decision for the MM study. When our deep maker ladder fills one side
naked, taker-buy the opposite side to form a matched pair — but only while the pair still costs
less than `threshold` (else merging would lock a loss, so we ride the naked residual instead)."""
from __future__ import annotations


def completion_buy(heavy_side: str, heavy_avg: float, light_ask: float,
                   naked_qty: float, threshold: float = 1.0):
    if naked_qty <= 0:
        return None
    if heavy_avg + light_ask >= threshold:
        return None
    light_side = "Down" if heavy_side == "Up" else "Up"
    return (light_side, float(naked_qty), float(light_ask))
