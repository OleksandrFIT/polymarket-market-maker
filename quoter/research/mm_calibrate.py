"""Calibrate the fill model (Theta) so the simulator reproduces the competitor's real
per-window end-state. Grid-search; report argmin loss so the caller can apply an
honesty gate (reject projections if the best fit is poor)."""
from __future__ import annotations

from quoter.research.mm_sim import simulate_window

_EPS = 1e-6


def window_loss(result, target: dict) -> float:
    loss = 0.0
    for got, key in ((result.gross_up, "size_up"), (result.gross_dn, "size_dn"),
                     (result.avg_up, "avg_up"), (result.avg_dn, "avg_dn")):
        want = float(target[key])
        denom = abs(want) + _EPS
        loss += ((got - want) / denom) ** 2
    return loss


def calibrate(cal_windows: list, targets: list, policy_fn, grid: list):
    per_theta = []
    best = None
    for theta in grid:
        total = 0.0
        for (tape, winner, ticks), tgt in zip(cal_windows, targets):
            r = simulate_window(tape, winner, policy_fn, theta, ticks)
            total += window_loss(r, tgt)
        per_theta.append((theta, total))
        if best is None or total < best[1]:
            best = (theta, total)
    return best[0], best[1], per_theta
