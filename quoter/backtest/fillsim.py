"""Deterministic fill model for the offline backtest.

Coarse on purpose (CLOB history is ~1 sample/min): a resting BUY bid fills
in full when its side's market price dips to/below the bid price over the
interval. Identical rule for every config so A/B comparisons are fair.

The engine re-quotes every interval, so a bid that stays below market fills
again each interval — absolute size/cost are inflated and not comparable to
live. Only the *relative* A/B direction is meaningful.
"""

from __future__ import annotations

from quoter.strategy.ladder import Quote

Fill = tuple[str, float, int]  # (side, price, size)


def simulate_interval_fills(
    quotes: list[Quote], yes_now: float, yes_next: float,
) -> list[Fill]:
    """Return the quotes that fill as YES price moves yes_now -> yes_next."""
    yes_low = min(yes_now, yes_next)
    no_low = 1.0 - max(yes_now, yes_next)
    fills: list[Fill] = []
    for q in quotes:
        side_low = yes_low if q.side == "YES" else no_low
        if side_low <= q.price:
            fills.append((q.side, q.price, q.size))
    return fills
