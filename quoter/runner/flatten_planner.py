"""Pure decision for a naked (one-sided) leg. No timing, no I/O — the live loop
owns the grace clock and calls this only once the grace has elapsed.

Prefer to COMPLETE the pair (buy the missing/light side) over selling, whenever
the completed pair would still cost < $1 — that locks a guaranteed $1 payout for
< $1, strictly better than dumping the naked leg. Only SELL (flatten) the heavy
side when completion is too expensive (pair >= $1) or the light side has no ask.
"""

from __future__ import annotations

from dataclasses import dataclass

Side = str  # "YES" | "NO"


@dataclass(frozen=True)
class NakedAction:
    kind: str   # "COMPLETE" (buy the light side) | "SELL" (sell the heavy side)
    side: Side  # COMPLETE: the light side to BUY; SELL: the heavy side to SELL
    qty: int    # shares == |naked|


def plan_naked_action(
    inv_yes: int, inv_no: int,
    yes_avg: float | None, no_avg: float | None,
    yes_ask: float | None, no_ask: float | None,
    naked_cap: int,
) -> NakedAction | None:
    """Decide what to do with a naked leg. None if |naked| < naked_cap."""
    naked = inv_yes - inv_no
    if abs(naked) < naked_cap:
        return None
    if naked > 0:
        heavy, light = "YES", "NO"
        heavy_avg, light_ask = yes_avg, no_ask
    else:
        heavy, light = "NO", "YES"
        heavy_avg, light_ask = no_avg, yes_ask
    qty = abs(naked)
    if (light_ask is not None and light_ask > 0 and heavy_avg is not None
            and (heavy_avg + light_ask) < 1.0):
        return NakedAction(kind="COMPLETE", side=light, qty=qty)
    return NakedAction(kind="SELL", side=heavy, qty=qty)
