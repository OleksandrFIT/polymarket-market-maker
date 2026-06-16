"""TIMING sweep on real tapes: find the wait-window W that best balances
'wait long enough to catch the 2nd leg (pair)' vs 'flatten the naked underdog
before it falls too far'. Reuses the cached tapes from the opportunity run.

Per window, from the REAL tape:
  - first time each side was sold <=0.49 (size>=5) = when we'd fill that leg
  - overall min price each side reached -> our maker fill price (0.46 if it got
    there, else 0.49)
  - price mark of a side at any time (last trade) -> the bid we'd flatten into

Policy(W): catch leg1 at its first dip. If the OTHER side dips within W -> PAIR
(hold to resolution, +edge). Else FLATTEN leg1 at price(t1+W) (cap the loss).
W=300 == ride to resolution (current behaviour).
"""
import statistics
from _real_tape_backtest import market_meta, tape, winner_binance

BID1, BID2 = 0.49, 0.46
SIZE_MIN = 5
SPREAD = 0.01
END_TS = 1781461800
N = 160


def my_fill(low):
    if low <= BID2:
        return BID2
    if low <= BID1:
        return BID1
    return None


def load(ts):
    meta = market_meta(ts)
    if not meta:
        return None
    cid, _, _ = meta
    tw = tape(ts, cid)
    if not tw:
        return None
    sells = {"Up": [], "Down": []}     # (t, px) SELLs size>=5
    marks = {"Up": [], "Down": []}     # (t, px) all trades -> price mark
    low = {"Up": 1.0, "Down": 1.0}
    for (t, out, side, px, sz) in tw:
        marks[out].append((t, px))
        if px < low[out]:
            low[out] = px
        if side == "SELL" and sz >= SIZE_MIN:
            sells[out].append((t, px))
    first = {}
    for s in ("Up", "Down"):
        d = [t for (t, px) in sells[s] if px <= BID1]
        first[s] = min(d) if d else None
    return {"ts": ts, "first": first, "low": low, "marks": marks,
            "winner": winner_binance(ts)}


def mark_at(marks, tt):
    p = None
    for (t, px) in marks:
        if t <= tt:
            p = px
        else:
            break
    return p


def pnl(win, W):
    fu, fd = win["first"]["Up"], win["first"]["Down"]
    if fu is None and fd is None:
        return 0.0, "sit"
    # leg1 = earlier dip
    if fd is None or (fu is not None and fu <= fd):
        leg1, t1, other, t2 = "Up", fu, "Down", fd
    else:
        leg1, t1, other, t2 = "Down", fd, "Up", fu
    if t2 is not None and t2 <= t1 + W:                 # PAIR caught in time
        cu, cd = my_fill(win["low"]["Up"]), my_fill(win["low"]["Down"])
        return 5 * (1.0 - cu - cd), "pair"
    # else NAKED -> flatten leg1 at t1+W
    f = my_fill(win["low"][leg1]) or BID1
    if W >= 300:                                        # ride to resolution
        payout = 5.0 if leg1 == win["winner"] else 0.0
        return payout - 5 * f, "ride"
    mk = mark_at(win["marks"][leg1], t1 + W)
    if mk is None:
        mk = f
    return 5 * ((mk - SPREAD) - f), "flat"


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

print("%-8s %9s %8s %7s %7s %7s %6s" % ("W(sec)", "net", "/win", "pairs%", "worst", "std", "<-2"))
for W in (20, 40, 60, 90, 120, 180, 240, 300):
    res = [pnl(w, W) for w in wins]
    ps = [p for p, _ in res]
    pairs = sum(1 for _, c in res if c == "pair")
    print("%-8d $%+8.2f $%+.3f  %5.1f%%  $%+5.2f  %5.2f  %4d" %
          (W, sum(ps), sum(ps) / n, 100 * pairs / n, min(ps),
           statistics.pstdev(ps), sum(1 for x in ps if x < -2)))
print()
print("W=300 = ride (current). Look for a W with net POSITIVE and high pairs%.")
print("If the best W stays negative -> no wait-gate rescues it (honest answer).")
