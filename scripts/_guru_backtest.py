"""Backtest the SYSTEMATIC guru strategy (hedge + tilt) on REAL 15m trade tapes.

Uses the SAME hedge_planner logic we will deploy:
  - wide two-sided maker ladder catches cheap fills on whichever side dips
  - each ~15s, buy the FAVORITE (taker at ask) toward favorite_shares == spent
  - hold to resolution; PnL = winning-side shares - spent
Reports win rate + PnL% of volume vs the guru's real 58% / +4.2%.

Reuses /tmp/poly_tape15_cache. Read-only.
"""
import sys, os, json, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quoter.runner.hedge_planner import hedge_buy_qty, favorite_side

CACHE = "/tmp/poly_tape15_cache"
LADDER = [round(0.50 - 0.05 * i, 2) for i in range(10)]   # 0.50..0.05 maker rungs/side
QMIN = 5
FILL = 5
CAP = 50.0            # $/window (win rate is scale-independent; PnL scales)
HEDGE_EVERY = 15      # seconds of window time between hedge actions
STEP = 10


def winner_of(tw):
    last_up = 0.5
    for (t, oc, sd, px, sz) in tw:
        if oc == "Up":
            last_up = px
        elif oc == "Down":
            last_up = 1 - px
    return "Up" if last_up >= 0.5 else "Down"


def sim(tw):
    base = tw[0][0]
    inv = {"Up": 0.0, "Down": 0.0}
    spent = 0.0
    bid = {"Up": 0.5, "Down": 0.5}
    ask = {"Up": 0.5, "Down": 0.5}
    next_hedge = 0
    for (t, oc, sd, px, sz) in tw:
        if oc not in ("Up", "Down"):
            continue
        trel = t - base
        other = "Down" if oc == "Up" else "Up"
        if sd == "BUY":
            ask[oc] = px
        else:
            bid[oc] = px
        # (1) maker fill on whichever side is being sold into our ladder
        if sd == "SELL" and sz >= QMIN and spent < CAP:
            myrung = max((r for r in LADDER if r <= bid[oc]), default=None)
            if myrung is not None and px <= myrung:
                inv[oc] += FILL
                spent += FILL * myrung
        # (2) hedge the favorite toward breakeven, throttled
        if trel >= next_hedge and spent < CAP:
            next_hedge = trel + HEDGE_EVERY
            fav = favorite_side(ask["Up"], ask["Down"]) or favorite_side(bid["Up"], bid["Down"])
            if fav:
                favk = "Up" if fav == "YES" else ("Down" if fav == "NO" else fav)
                # favorite_side returns YES/NO; map to Up/Down (YES=Up, NO=Down)
                favk = "Up" if fav == "YES" else "Down"
                q = hedge_buy_qty(inv[favk], spent, ask[favk], STEP, CAP - spent)
                q = float(int(q))
                if q > 0:
                    inv[favk] += q
                    spent += q * ask[favk]
    return inv, spent


wins = []
for fn in os.listdir(CACHE):
    tw = json.load(open(os.path.join(CACHE, fn)))
    if not tw or len(tw) < 20:
        continue
    wins.append(tw)

pnls = []
vol = 0.0
for tw in wins:
    inv, spent = sim(tw)
    if spent < FILL * 0.05:
        continue
    w = winner_of(tw)
    wk = "Up" if w == "Up" else "Down"
    payout = inv[wk]
    pnl = payout - spent
    pnls.append(pnl)
    vol += spent

n = len(pnls)
wr = 100 * sum(1 for p in pnls if p > 0) / n
print("=== SYSTEMATIC GURU STRATEGY backtest — %d real windows ===" % n)
print("win rate      : %.0f%%   (guru real: 58%%)" % wr)
print("total PnL      : $%+.1f   on $%.0f volume = %+.1f%%   (guru real: +4.2%%)"
      % (sum(pnls), vol, 100 * sum(pnls) / vol if vol else 0))
print("avg PnL/window : $%+.2f" % (sum(pnls) / n))
print("best / worst   : $%+.1f / $%+.1f" % (max(pnls), min(pnls)))
print("median         : $%+.2f" % statistics.median(pnls))
