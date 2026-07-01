"""Pure single-window MM simulator: replay the taker tape, fill our resting bids,
merge matched pairs, hold residual to resolution, compute PnL."""
from __future__ import annotations

from quoter.research.mm_fill import fill
from quoter.research.mm_types import Theta, WindowResult


def simulate_window(tape: list, winner: str, policy_fn, theta: Theta,
                    ticks: list) -> WindowResult:
    live = {"Up": 0.0, "Down": 0.0}       # inventory after merges
    gross = {"Up": 0.0, "Down": 0.0}      # cumulative fills (never reduced)
    gcost = {"Up": 0.0, "Down": 0.0}      # cumulative cost, for avg price
    spent = 0.0
    returned = 0.0
    last_ts = (tape[-1]["ts"] + 1) if tape else 0

    for i, (ts, mid) in enumerate(ticks):
        end = ticks[i + 1][0] if i + 1 < len(ticks) else last_ts
        sl = [t for t in tape if ts <= t["ts"] < end + theta.lag]
        for q in policy_fn(mid, live):
            fr = fill(q.side, q.price, q.size, sl, theta)
            if fr.filled > 0:
                c = fr.filled * q.price
                live[q.side] += fr.filled
                gross[q.side] += fr.filled
                gcost[q.side] += c
                spent += c
        # merge matched pairs -> each pair redeems for 1.0
        m = min(live["Up"], live["Down"])
        if m > 0:
            live["Up"] -= m
            live["Down"] -= m
            returned += m

    # resolution: winner's remaining live shares redeem at 1.0; loser expires
    returned += live[winner]
    loser = "Down" if winner == "Up" else "Up"
    adverse = live[loser]

    avg_up = gcost["Up"] / gross["Up"] if gross["Up"] else 0.0
    avg_dn = gcost["Down"] / gross["Down"] if gross["Down"] else 0.0
    return WindowResult(
        gross_up=gross["Up"], gross_dn=gross["Down"],
        avg_up=avg_up, avg_dn=avg_dn, pair_cost=avg_up + avg_dn,
        spent=spent, returned=returned, pnl=returned - spent, adverse=adverse,
    )
