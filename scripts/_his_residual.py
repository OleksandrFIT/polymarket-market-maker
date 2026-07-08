"""Reverse-engineer 0xb27b's RESIDUAL-CONTROL system from the activity collector.
Per window, reconstruct his running inventory from the time-ordered events and split PnL:
  merge_pnl  = merged * (1 - pair_cost)      # the <$1 spread he captures (the real edge)
  residual_pnl = total_cash_pnl - merge_pnl  # what the unmatched leg does to it
Then classify the TERMINAL residual (net-long winner=redeems / loser=expires / flat), his
redeem & sell behavior, and how naked he lets himself get (max intra-window imbalance).
Tests the user's thesis: he earns from the WINDOWS -> residual_pnl should NOT be ~-merge_pnl.
Usage: python3 scripts/_his_residual.py <competitor_jsonl>"""
import sys
import json
import collections
import statistics as st

from quoter.research.mm_tape import load_window

COMP = sys.argv[1]

comp = collections.defaultdict(list)
for l in open(COMP):
    l = l.strip()
    if not l:
        continue
    try:
        r = json.loads(l)
    except json.JSONDecodeError:
        continue
    s = r.get("slug", "")
    if isinstance(s, str) and s.startswith("btc-updown-5m-"):
        comp[s].append(r)

rows = []
for slug, evs in sorted(comp.items()):
    w = load_window(slug)
    if not w or w[1] not in ("Up", "Down"):
        continue
    winner = w[1]
    loser = "Down" if winner == "Up" else "Up"
    evs.sort(key=lambda x: x.get("timestamp", 0))
    inv = {"Up": 0.0, "Down": 0.0}
    buy = up = dn = cu = cdn = merged = sell_ret = redeem_ret = merge_ret = 0.0
    sold = 0.0
    max_imb = 0.0
    for e in evs:
        t = e.get("type")
        sz = float(e.get("size", 0) or 0)
        u = float(e.get("usdcSize", 0) or 0)
        out = e.get("outcome")
        if t == "TRADE" and e.get("side") == "BUY":
            buy += u
            inv[out] = inv.get(out, 0.0) + sz
            if out == "Up":
                up += sz
                cu += u
            else:
                dn += sz
                cdn += u
        elif t == "TRADE" and e.get("side") == "SELL":
            sell_ret += u
            sold += sz
            if out in inv:
                inv[out] -= sz
        elif t == "MERGE":
            merged += sz
            merge_ret += u
            inv["Up"] -= sz
            inv["Down"] -= sz
        elif t == "REDEEM":
            redeem_ret += u
        max_imb = max(max_imb, abs(inv["Up"] - inv["Down"]))
    if buy <= 0 or merged <= 0:
        continue
    pair_cost = (cu / up if up else 0) + (cdn / dn if dn else 0)
    merge_pnl = merged * (1.0 - pair_cost)
    total_pnl = (merge_ret + redeem_ret + sell_ret) - buy
    residual_pnl = total_pnl - merge_pnl
    term = inv["Up"] - inv["Down"]                       # terminal net imbalance (pre-resolution)
    term_side = "Up" if term > 0.5 else ("Down" if term < -0.5 else "flat")
    rows.append({"slug": slug, "merge_pnl": merge_pnl, "residual_pnl": residual_pnl,
                 "total_pnl": total_pnl, "pair_cost": pair_cost, "merged": merged,
                 "redeem": redeem_ret, "sold": sold, "max_imb": max_imb,
                 "term_side": term_side, "winner": winner, "loser": loser,
                 "term_is_winner": (term_side == winner),
                 "term_is_loser": (term_side == loser)})

rows = rows[1:]                                          # drop partial first window
n = len(rows)
print("0xb27b residual system — %d resolved windows\n" % n)

print("PNL DECOMPOSITION (per window, mean):")
print("  merge_pnl (spread edge):   $%+.2f" % (sum(r["merge_pnl"] for r in rows) / n))
print("  residual_pnl (the leg):    $%+.2f" % (sum(r["residual_pnl"] for r in rows) / n))
print("  total_pnl:                 $%+.2f" % (sum(r["total_pnl"] for r in rows) / n))
mp = sum(r["merge_pnl"] for r in rows); rp = sum(r["residual_pnl"] for r in rows)
print("  --> residual eats %.0f%% of the merge edge (100%% = fully cancels)" % (-100 * rp / mp if mp else 0))

print("\nTERMINAL RESIDUAL (how he ends the window):")
print("  net-long WINNER (redeems $1): %d/%d = %.0f%%" % (sum(r["term_is_winner"] for r in rows), n, 100 * sum(r["term_is_winner"] for r in rows) / n))
print("  net-long LOSER (expires $0):  %d/%d = %.0f%%" % (sum(r["term_is_loser"] for r in rows), n, 100 * sum(r["term_is_loser"] for r in rows) / n))
print("  ~flat (fully paired):         %d/%d = %.0f%%" % (sum(1 for r in rows if r["term_side"] == "flat"), n, 100 * sum(1 for r in rows if r["term_side"] == "flat") / n))

print("\nRESIDUAL DISPOSITION:")
print("  redeem $/window (winner cashed):  $%.2f" % (sum(r["redeem"] for r in rows) / n))
print("  windows with any SELL:            %d/%d = %.0f%%" % (sum(1 for r in rows if r["sold"] > 0.5), n, 100 * sum(1 for r in rows if r["sold"] > 0.5) / n))
print("  sold shares/window (mean):        %.1f" % (sum(r["sold"] for r in rows) / n))
print("  max intra-window imbalance:       mean %.0f  median %.0f  max %.0f shares" % (
    sum(r["max_imb"] for r in rows) / n, st.median([r["max_imb"] for r in rows]), max(r["max_imb"] for r in rows)))
print("  (his imbalance in $ ~ x avg 0.5 -> naked depth $%.0f mean)" % (0.5 * sum(r["max_imb"] for r in rows) / n))

# is residual_pnl positive when he ends net-long winner?
win_rows = [r for r in rows if r["term_is_winner"]]
los_rows = [r for r in rows if r["term_is_loser"]]
if win_rows:
    print("\n  residual_pnl when ends net-long WINNER: $%+.2f (n=%d)" % (sum(r["residual_pnl"] for r in win_rows) / len(win_rows), len(win_rows)))
if los_rows:
    print("  residual_pnl when ends net-long LOSER:  $%+.2f (n=%d)" % (sum(r["residual_pnl"] for r in los_rows) / len(los_rows), len(los_rows)))
