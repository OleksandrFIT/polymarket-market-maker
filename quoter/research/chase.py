"""Causal momentum-chase signal for the MM backtest. Pure + unit-tested.
Given the Up-side mid history seen SO FAR, name the side that has been rising (the
momentum favorite) over the last `lookback_sec`, or None if the move is below
`threshold`. Never reads future ticks — the caller passes only history up to `now_ts`."""
from __future__ import annotations


def chase_signal(mid_hist, now_ts, lookback_sec, threshold):
    """mid_hist: list of (ts, up_mid) in time order, up to and including now.
    Return "Up" if up_mid rose >= threshold over the last lookback_sec, "Down" if it
    fell >= threshold, else None. (up_mid + down_mid == 1, so Up rising == Down falling.)"""
    if not mid_hist:
        return None
    cur = mid_hist[-1][1]
    cutoff = now_ts - lookback_sec
    past = None
    for ts, m in mid_hist:
        if ts <= cutoff:
            past = m            # last sample at/before the lookback edge
        else:
            break
    if past is None:
        past = mid_hist[0][1]   # history shorter than lookback: use earliest sample
    delta = cur - past
    if delta >= threshold:
        return "Up"
    if delta <= -threshold:
        return "Down"
    return None
