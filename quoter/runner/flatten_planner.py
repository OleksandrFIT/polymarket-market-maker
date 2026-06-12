"""Pure decision for the auto-flat (kill-naked) mode. No timing, no I/O — the
live loop owns the grace clock and calls this only once the grace has elapsed.

The naked leg (one-sided inventory) is bought on the cheapening side, which in a
trend is the LOSING side (adverse selection: a dip is cheap because that side is
losing). This decides how much of the heavy side to SELL to flatten back to the
paired core. Selling exactly |naked| shares leaves min(inv_yes, inv_no) on each
side — the pairs — untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

Side = str  # "YES" | "NO"


@dataclass(frozen=True)
class FlattenDecision:
    side: Side  # the heavy side to sell
    qty: int    # shares to sell == |naked|


def plan_flatten(inv_yes: int, inv_no: int, naked_cap: int) -> FlattenDecision | None:
    """Return the naked excess to sell, or None if |naked| < naked_cap. Selling
    qty == |naked| of the heavy side leaves the paired core intact."""
    naked = inv_yes - inv_no
    if abs(naked) < naked_cap:
        return None
    if naked > 0:
        return FlattenDecision(side="YES", qty=naked)
    return FlattenDecision(side="NO", qty=-naked)
