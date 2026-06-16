"""Make-or-break check: does the strong-leader edge survive the REAL executable
ask? Instead of assuming cost = mid + 1c, use the ACTUAL price takers paid to
BUY the favorite around minute 2 (from the real trade tape). If real fills are
much worse than mid+1c, the edge dies.

Reuses the 160 cached tapes. Read-only.
"""
import statistics
from _real_tape_backtest import market_meta, tape, winner_binance

END_TS = 1781461800
N = 160
THR = 0.60
M = 2                       # decision minute (3 min left)


def load(ts):
    meta = market_meta(ts)
    if not meta:
        return None
    cid, _, _ = meta
    tw = tape(ts, cid)
    if not tw:
        return None
    # per-minute last Up price (favorite detection)
    upmin = [None] * 5
    for (t, out, side, p, sz) in tw:
        if out == "Up":
            m = int((t - ts) // 60)
            if 0 <= m < 5:
                upmin[m] = p
    last = 0.5
    for m in range(5):
        if upmin[m] is None:
            upmin[m] = last
        last = upmin[m]
    winner = winner_binance(ts)
    return {"ts": ts, "upmin": upmin, "tw": tw, "winner": winner}


def real_buy_price(tw, ts, side, m, min_size=5):
    """actual price a taker paid BUYING `side` during minute m (real ask hit).
    Returns size-weighted avg of BUY trades on that side in [ts+60m, ts+60(m+1))."""
    lo, hi = ts + 60 * m, ts + 60 * (m + 1)
    num = den = 0.0
    for (t, out, sd, p, sz) in tw:
        if out == side and sd == "BUY" and lo <= t < hi and sz >= 1:
            num += p * sz; den += sz
    return (num / den) if den else None


print("loading %d tapes (cached)..." % N)
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

# compare: modelled (mid+1c) vs REAL executable ask
model_tr = []
real_tr = []
spreads = []
for w in wins:
    up = w["upmin"][M]
    side = "Up" if up > 0.5 else "Down"
    fav_mid = up if side == "Up" else 1 - up
    if fav_mid <= THR:
        continue
    won = (side == w["winner"])
    model_tr.append((fav_mid + 0.01, won))
    rp = real_buy_price(w["tw"], w["ts"], side, M)
    if rp is not None:
        real_tr.append((rp, won))
        spreads.append(rp - fav_mid)


def summ(tr):
    hr = sum(1 for _, x in tr if x) / len(tr)
    cost = statistics.mean(c for c, _ in tr)
    return len(tr), hr, cost, hr - cost


print("=== strong leader >%.2f @ minute %d : MODEL vs REAL executable ask ===" % (THR, M))
mn, mh, mc, mev = summ(model_tr)
print(" MODELLED (mid+1c): n=%d hit=%.1f%% cost=$%.3f EV=$%+.4f/sh" % (mn, 100*mh, mc, mev))
if real_tr:
    rn, rh, rc, rev = summ(real_tr)
    print(" REAL ask paid:     n=%d hit=%.1f%% cost=$%.3f EV=$%+.4f/sh" % (rn, 100*rh, rc, rev))
    print(" avg real spread over mid: $%+.3f (median $%+.3f)" % (statistics.mean(spreads), statistics.median(spreads)))
    print()
    print(" 5-share EV/window (real): $%+.3f | risk per loss ~$%.2f" % (5*rev, 5*rc))
    print()
    if rev > 0.005:
        print(" >>> EDGE SURVIVES real fills (+EV). Worth pursuing.")
    elif rev > -0.005:
        print(" >>> EDGE ~BREAKEVEN at real fills. Spread eats most of it.")
    else:
        print(" >>> EDGE DIES at real fills. Not executable. Honest dead-end.")
