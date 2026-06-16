"""MARKET-WIDE opportunity analysis on REAL Polymarket trade tapes.

Does NOT guess our fills (queue position is unknowable). Instead measures, per
real window, what the market actually offered:
  - lowest price each side (Up/Down) was actually SOLD at with size >= 5
    (i.e. where a real seller hit down to -> where a maker bid COULD fill)
  - did BOTH sides dip to <=0.49 (chop -> a cheap PAIR was catchable) or only
    ONE (trend -> you'd be stuck naked on the dipping = underdog side)?
  - the real winner -> outcome of each case.

This is the honest core question: how often is the cheap-pair edge actually
available, and is being stuck naked a winner or loser on average.
Read-only; reuses tape cache from _real_tape_backtest.
"""
import time, statistics
from _real_tape_backtest import market_meta, tape, winner_binance

SIZE_MIN = 5          # a sell must be >= our size to count as a fill opportunity
BID1, BID2 = 0.49, 0.46

END_TS = 1781461800   # most recent window we know; walk backwards
N = 160


def analyze(ts):
    meta = market_meta(ts)
    if not meta:
        return None
    cid, _, _ = meta
    tw = tape(ts, cid)
    if not tw:
        return None
    # lowest SELL price (size>=SIZE_MIN) each side = realistic maker-fill level
    low = {"Up": 1.0, "Down": 1.0}
    for (t, out, side, px, sz) in tw:
        if side == "SELL" and sz >= SIZE_MIN and px < low[out]:
            low[out] = px
    winner = winner_binance(ts)
    up_dip = low["Up"] <= BID1
    dn_dip = low["Down"] <= BID1
    return {"ts": ts, "low_up": low["Up"], "low_dn": low["Down"],
            "up_dip": up_dip, "dn_dip": dn_dip, "winner": winner}


rows = []
ts = END_TS
got = 0
miss = 0
while got < N and miss < 40:
    r = None
    try:
        r = analyze(ts)
    except Exception:
        r = None
    if r:
        rows.append(r); got += 1
    else:
        miss += 1
    ts -= 300
    time.sleep(0.05)

n = len(rows)
print("=== MARKET OPPORTUNITY — %d real windows ===" % n)
print("(low_up/low_dn = lowest price each side was really sold at, size>=5)")
print()

both = [r for r in rows if r["up_dip"] and r["dn_dip"]]
one = [r for r in rows if r["up_dip"] != r["dn_dip"]]
neither = [r for r in rows if not r["up_dip"] and not r["dn_dip"]]

print("PAIR catchable (both sides dipped <=0.49): %3d (%4.1f%%)" % (len(both), 100*len(both)/n))
print("ONE-sided   (only one dipped -> naked):    %3d (%4.1f%%)" % (len(one), 100*len(one)/n))
print("neither     (no dip, sit out):             %3d (%4.1f%%)" % (len(neither), 100*len(neither)/n))
print()

# PAIR economics: cheapest realistic pair = max(low_up,0.46-ish) ... model buying
# each leg at our bid that it reached: 0.46 if it dipped <=0.46 else 0.49.
def my_fill(low):
    if low <= BID2:
        return BID2
    if low <= BID1:
        return BID1
    return None

pair_edges = []
for r in both:
    cu = my_fill(r["low_up"]); cd = my_fill(r["low_dn"])
    if cu and cd:
        pair_edges.append(1.0 - (cu + cd))   # per-share pair edge ($1 payout - cost)
if pair_edges:
    print("PAIR case: avg pair cost $%.3f -> edge $%+.3f/share (x5 = $%+.2f/rung-pair)" %
          (1 - statistics.mean(pair_edges), statistics.mean(pair_edges), statistics.mean(pair_edges)*5))
    print("           every pair-window is +EV regardless of direction.")
print()

# ONE-SIDED economics: you hold the dipping (underdog) side naked. Does it win?
naked_win = 0
naked_pnl = []
for r in one:
    held = "Up" if r["up_dip"] else "Down"
    fill = my_fill(r["low_up"] if held == "Up" else r["low_dn"])
    won = (held == r["winner"])
    if won:
        naked_win += 1
    # 5 naked shares: win -> 5*(1-fill); lose -> -5*fill
    naked_pnl.append(5*(1-fill) if won else -5*fill)
if one:
    print("ONE-SIDED case: held-side WON %d/%d (%4.1f%%)  [<50%% = naked loses on avg]" %
          (naked_win, len(one), 100*naked_win/len(one)))
    print("                avg naked PnL (5 sh) $%+.2f/window" % statistics.mean(naked_pnl))
print()

# Crude blended market EV if you played every window (pair when catchable, else naked)
blend = []
for r in rows:
    if r["up_dip"] and r["dn_dip"]:
        cu = my_fill(r["low_up"]); cd = my_fill(r["low_dn"])
        blend.append(5*(1.0-(cu+cd)))
    elif r["up_dip"] != r["dn_dip"]:
        held = "Up" if r["up_dip"] else "Down"
        fill = my_fill(r["low_up"] if held == "Up" else r["low_dn"])
        blend.append(5*(1-fill) if held == r["winner"] else -5*fill)
    else:
        blend.append(0.0)
print("BLENDED (pair if catchable, else naked, 5-share):")
print("  net $%+.2f over %d win = $%+.3f/win | std %.2f | worst $%+.2f | win<-$2: %d" %
      (sum(blend), n, sum(blend)/n, statistics.pstdev(blend), min(blend),
       sum(1 for x in blend if x < -2)))
