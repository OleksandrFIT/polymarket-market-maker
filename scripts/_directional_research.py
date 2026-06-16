"""Directional-entry research on REAL Polymarket price paths.

Core question: is there ANY entry rule whose realized hit-rate beats its entry
cost (a real inefficiency = +EV)? If the market is efficient, every rule is
~breakeven minus spread. We test momentum, leader, strong-leader, and a control
(buy the underdog = our current losing maker-fill) which MUST be -EV to validate.

EV per share = hit_rate*$1 - avg_entry_cost  (buy side S, win $1 if S wins).
Taker entry modelled at price + SPREAD.
Reuses cached tapes. Read-only.
"""
import statistics
from _real_tape_backtest import market_meta, tape, winner_binance

SPREAD = 0.01
END_TS = 1781461800
N = 160


def up_path(tw, ts):
    """per-minute last Up price (0..4), forward-filled."""
    px = [None] * 5
    for (t, out, side, p, sz) in tw:
        if out != "Up":
            continue
        m = int((t - ts) // 60)
        if 0 <= m < 5:
            px[m] = p
    last = 0.5
    for m in range(5):
        if px[m] is None:
            px[m] = last
        last = px[m]
    return px


def load(ts):
    meta = market_meta(ts)
    if not meta:
        return None
    cid, _, _ = meta
    tw = tape(ts, cid)
    if not tw:
        return None
    return {"up": up_path(tw, ts), "winner": winner_binance(ts)}


print("loading %d windows (cached)..." % N)
wins = []
ts = END_TS
miss = 0
while len(wins) < N and miss < 40:
    try:
        w = load(ts)
    except Exception:
        w = None
    if w:
        wins.append(w)
    else:
        miss += 1
    ts -= 300
n = len(wins)
print("loaded %d\n" % n)


def evround(trades):
    """trades = list of (entry_cost, won_bool). print hit-rate, cost, EV."""
    if not trades:
        return "  (no trades)"
    hr = sum(1 for _, w in trades if w) / len(trades)
    cost = statistics.mean(c for c, _ in trades)
    ev = hr - cost
    return "n=%4d hit=%4.1f%% cost=$%.3f EV=$%+.4f/sh %s" % (
        len(trades), 100 * hr, cost, ev, "<<< +EV" if ev > 0.005 else "")


def side_price(w, m, side):
    return w["up"][m] if side == "Up" else (1.0 - w["up"][m])


print("=== CONTROL: buy the UNDERDOG (our maker-fill = the dipping side) ===")
for m in (1, 2, 3):
    tr = []
    for w in wins:
        up = w["up"][m]
        side = "Up" if up < 0.5 else "Down"          # the cheap/dipping side
        cost = side_price(w, m, side) + SPREAD
        if cost < 0.5:
            tr.append((cost, side == w["winner"]))
    print(" underdog@m=%d: %s" % (m, evround(tr)))

print()
print("=== LEADER: buy the current favorite (>0.5) at minute m, hold ===")
for m in (1, 2, 3, 4):
    tr = []
    for w in wins:
        up = w["up"][m]
        side = "Up" if up > 0.5 else "Down"
        cost = side_price(w, m, side) + SPREAD
        tr.append((cost, side == w["winner"]))
    print(" leader@m=%d:  %s" % (m, evround(tr)))

print()
print("=== STRONG LEADER: buy favorite only if price>thr at minute m ===")
for m in (2, 3, 4):
    for thr in (0.60, 0.70, 0.80):
        tr = []
        for w in wins:
            up = w["up"][m]
            side = "Up" if up > 0.5 else "Down"
            p = side_price(w, m, side)
            if p > thr:
                tr.append((p + SPREAD, side == w["winner"]))
        print(" strong@m=%d thr=%.2f: %s" % (m, thr, evround(tr)))

print()
print("=== MOMENTUM: buy the side whose price ROSE most over [m-1,m] ===")
for m in (2, 3, 4):
    tr = []
    for w in wins:
        dup = w["up"][m] - w["up"][m - 1]
        side = "Up" if dup > 0 else "Down"           # the rising side
        if abs(dup) < 0.02:
            continue                                  # need real momentum
        cost = side_price(w, m, side) + SPREAD
        tr.append((cost, side == w["winner"]))
    print(" momentum@m=%d: %s" % (m, evround(tr)))

print()
print("EV>0 => an inefficiency we could harvest. EV~0/neg => market efficient,")
print("directional has no free edge either. Control (underdog) should be clearly -EV.")
