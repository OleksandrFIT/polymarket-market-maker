"""Calibrate the fill model (Theta) so the simulator reproduces the competitor's real
per-window end-state. Grid-search; report argmin loss so the caller can apply an
honesty gate (reject projections if the best fit is poor)."""
from __future__ import annotations

from quoter.research.mm_sim import simulate_window

_EPS = 1e-6


def realized_pnl(target, winner):
    """Competitor's actual realized PnL for one window from his real per-side fills.
    target: {size_up,avg_up,size_dn,avg_dn}; winner: 'Up'|'Down'.
    spent = size_up*avg_up + size_dn*avg_dn; returned = size of winning side; pnl = returned-spent."""
    su = float(target["size_up"])
    au = float(target["avg_up"])
    sd = float(target["size_dn"])
    ad = float(target["avg_dn"])
    spent = su * au + sd * ad
    returned = su if winner == "Up" else sd
    return returned - spent


def window_loss(result, target: dict) -> float:
    loss = 0.0
    # size terms: relative squared error
    for got, want in ((result.gross_up, target["size_up"]), (result.gross_dn, target["size_dn"])):
        denom = abs(float(want)) + _EPS
        loss += ((got - float(want)) / denom) ** 2
    # price terms: absolute squared error (prices in [0,1], no relative blow-up)
    for got, want in ((result.avg_up, target["avg_up"]), (result.avg_dn, target["avg_dn"])):
        loss += (got - float(want)) ** 2
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
