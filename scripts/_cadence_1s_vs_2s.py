"""1s vs 2s requote cadence — measure the UPSIDE of a faster loop from the real tape.

The faithful fill-level A/B needs recorded 1s quotes (we only have 2s). But the DOMINANT
driver of a faster loop is how much the market price 'runs away' during the seconds our
quote sits unchanged — a 2s loop leaves us stale up to 2s, a 1s loop up to 1s. That drift
is measurable directly from the real per-trade tape (no book depth needed):

For each recent RESOLVED 5m window we build a 1s last-trade price grid (Up outcome) and
measure, per window and pooled:
  - reprice rate: fraction of ticks where our quote (best≈price, rounded to tick 0.001)
    would CHANGE vs the prior tick — at 1s vs 2s cadence. How often 1s issues a new quote.
  - staleness drift: mean |Δprice| the market moves across a 1s vs a 2s gap. The EXTRA
    adverse exposure a 2s loop carries ≈ the drift in the 2nd second that 1s would cut.
  - same split for the LAST 60s of the window (where a binary's price swings hardest).

Usage: python3 scripts/_cadence_1s_vs_2s.py [n_windows]
"""
import sys
import time

from quoter.research.mm_tape import load_window

TICK = 0.001
N = int(sys.argv[1]) if len(sys.argv) > 1 else 60
NOW = int(time.time())
# most-recent fully-resolved 5m windows: floor to 300, back off 2 windows for settle
BASE = (NOW // 300) * 300 - 600


def price_grid(tape, open_ts, end_ts):
    """1s grid of last Up-trade price in [open_ts, end_ts]."""
    ups = [(t["ts"], t["price"]) for t in tape if t["oi"] == 0]
    ups.sort()
    grid = []
    j = 0
    last = 0.5
    for ts in range(int(open_ts), int(end_ts) + 1):
        while j < len(ups) and ups[j][0] <= ts:
            last = ups[j][1]
            j += 1
        grid.append(last)
    return grid


def stats_for(grid, near_end_from=None):
    """reprice-rate + mean drift at 1s and 2s over a grid (optionally tail slice)."""
    g = grid if near_end_from is None else grid[-near_end_from:]
    if len(g) < 3:
        return None
    q = [round(p, 3) for p in g]                      # our quote anchor, tick-rounded
    # reprice rate: quote changes between consecutive samples
    rp1 = sum(1 for i in range(1, len(q)) if q[i] != q[i - 1]) / (len(q) - 1)
    q2 = q[::2]
    rp2 = sum(1 for i in range(1, len(q2)) if q2[i] != q2[i - 1]) / max(len(q2) - 1, 1)
    # drift: mean |Δ| over 1s and 2s gaps
    d1 = sum(abs(g[i] - g[i - 1]) for i in range(1, len(g))) / (len(g) - 1)
    d2 = sum(abs(g[i] - g[i - 2]) for i in range(2, len(g))) / max(len(g) - 2, 1)
    return rp1, rp2, d1, d2


def main():
    pool = {"rp1": [], "rp2": [], "d1": [], "d2": [],
            "e_rp1": [], "e_rp2": [], "e_d1": [], "e_d2": []}
    used = 0
    for k in range(N):
        open_ts = BASE - k * 300
        slug = f"btc-updown-5m-{open_ts}"
        w = load_window(slug)
        if not w or not w[0]:
            continue
        tape, winner, ots = w
        if not tape:
            continue
        end_ts = min(open_ts + 300, tape[-1]["ts"])
        grid = price_grid(tape, open_ts, end_ts)
        s = stats_for(grid)
        e = stats_for(grid, near_end_from=60)
        if not s:
            continue
        used += 1
        pool["rp1"].append(s[0]); pool["rp2"].append(s[1])
        pool["d1"].append(s[2]); pool["d2"].append(s[3])
        if e:
            pool["e_rp1"].append(e[0]); pool["e_rp2"].append(e[1])
            pool["e_d1"].append(e[2]); pool["e_d2"].append(e[3])

    if not used:
        print("no resolved windows loaded (data-api reachable? cache?)")
        return

    def avg(x):
        return sum(x) / len(x) if x else float("nan")

    print(f"windows analysed: {used}  (tick={TICK})\n")
    print("WHOLE WINDOW:")
    print(f"  reprice rate  1s={avg(pool['rp1'])*100:5.1f}%   2s={avg(pool['rp2'])*100:5.1f}%")
    print(f"  mean drift    1s={avg(pool['d1'])*100:6.3f}¢   2s={avg(pool['d2'])*100:6.3f}¢")
    print(f"  extra staleness a 2s loop carries ≈ {(avg(pool['d2'])-avg(pool['d1']))*100:6.3f}¢/quote")
    print("\nLAST 60s (binary swings hardest here):")
    print(f"  reprice rate  1s={avg(pool['e_rp1'])*100:5.1f}%   2s={avg(pool['e_rp2'])*100:5.1f}%")
    print(f"  mean drift    1s={avg(pool['e_d1'])*100:6.3f}¢   2s={avg(pool['e_d2'])*100:6.3f}¢")
    print(f"  extra staleness (2s vs 1s) ≈ {(avg(pool['e_d2'])-avg(pool['e_d1']))*100:6.3f}¢/quote")


if __name__ == "__main__":
    main()
