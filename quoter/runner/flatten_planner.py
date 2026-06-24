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


def naked_action_due(cfg, naked: int, time_remaining: float,
                     naked_since_heavy: float | None, now: float) -> bool:
    """Pure: should the loop act on a naked leg (COMPLETE/SELL) this tick?

    complete_pairs: act in the late window (last complete_gate_sec) on ANY naked.
    auto_flat (legacy, unchanged): act once naked has stood at >= naked_cap past
    flatten_grace_sec, or the window is within flatten_grace_sec of the end.
    """
    if naked == 0:
        return False
    if cfg.complete_pairs:
        # continuous: act THROUGHOUT the window (small steps, balanced as we go);
        # the SELL fallback is still gated to near-end by the live loop.
        if getattr(cfg, "complete_continuous", False):
            return True
        return time_remaining <= cfg.complete_gate_sec
    if cfg.auto_flat:
        if abs(naked) < cfg.naked_cap or naked_since_heavy is None:
            return False
        return ((now - naked_since_heavy) >= cfg.flatten_grace_sec
                or time_remaining <= cfg.flatten_grace_sec)
    return False


def complete_cap_qty(requested: float, already_completed: float, cap: float) -> float:
    """Lag-proof bound on taker-COMPLETE buys: never let cumulative completes on a
    side exceed ``cap`` (mirrors the maker post_cap). Stops the completion logic
    from chasing a crashing light side into an over-bought naked loser — live
    window 1781627400 completed 20 Down vs 10 Up because a falling light leg keeps
    satisfying heavy_avg + light_ask < $1, and the gate (near_end) bypassed the
    cooldown so it re-bought every tick. Returns 0 when already at/over the cap."""
    return max(0.0, min(requested, cap - already_completed))


def recent_complete_qty(completes: list[tuple[float, float]], now: float,
                        grace: float) -> float:
    """Sum of completion qtys still inside the feed-lag window — the inventory read
    may not yet reflect them, so subtract them from the naked to avoid double-buying.
    Completes OLDER than ``grace`` are already absorbed into the naked count, so they
    drop out — which is what makes CONTINUOUS completion lag-safe (unlike a cumulative
    total, which would wrongly cancel legitimately-new naked later in the window)."""
    return sum(q for (t, q) in completes if (now - t) < grace)


def balance_complete_qty(naked: float, already_completed: float) -> float:
    """Tighter completion cap: complete only enough to BALANCE the pair (reach the
    heavy side), accounting for completes the lagging inventory hasn't absorbed yet
    (``already_completed``). Light never exceeds heavy -> zero excess naked loser.
    This is stricter than complete_cap_qty's fixed cap: it tracks the actual naked,
    so a reversing/chop window can't leave a directional tilt (unlike the
    competitor, who keeps one)."""
    return max(0.0, naked - already_completed)
