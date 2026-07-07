"""Detailed per-window breakdown of 0xb27b's behavior from the activity collector.
Everything here is computable from /activity alone (buy/merge/redeem) — no book/tape/winner
needed (pnl = returns - buy). Read-only.
Usage: python3 scripts/_comp_detail.py <competitor_jsonl>"""
import sys
import json
import collections
import statistics as st

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
tot_trades = 0
for slug, evs in comp.items():
    buy = up = dn = merged = redeem = sells = 0.0
    ntr = 0
    up_cost = dn_cost = 0.0
    for e in evs:
        t = e.get("type")
        sz = float(e.get("size", 0) or 0)
        u = float(e.get("usdcSize", 0) or 0)
        pr = float(e.get("price", 0) or 0)
        if t == "TRADE" and e.get("side") == "BUY":
            buy += u
            ntr += 1
            if e.get("outcome") == "Up":
                up += sz
                up_cost += u
            else:
                dn += sz
                dn_cost += u
        elif t == "TRADE" and e.get("side") == "SELL":
            sells += u
            ntr += 1
        elif t == "MERGE":
            merged += sz
        elif t == "REDEEM":
            redeem += u
    ret = merged + redeem + sells        # merge returns $1/pair; redeem $1/share; sells cash
    pnl = ret - buy
    bought = up + dn
    tot_trades += ntr
    rows.append({
        "slug": slug, "ntr": ntr, "buy": buy, "up": up, "dn": dn, "bought": bought,
        "merged": merged, "redeem": redeem, "sells": sells, "pnl": pnl,
        "pair_cost": (buy / merged) if merged else None,
        "up_avg": (up_cost / up) if up else None, "dn_avg": (dn_cost / dn) if dn else None,
        "match": (2 * merged / bought) if bought else 0,
        "up_frac": (up / bought) if bought else None,
    })

# drop the partial first window (collection started mid-window) for clean aggregates
rows.sort(key=lambda r: r["slug"])
clean = rows[1:] if len(rows) > 1 else rows
n = len(clean)

buys = sum(r["buy"] for r in clean)
pnls = [r["pnl"] for r in clean]
merg = sum(r["merged"] for r in clean)
red = sum(r["redeem"] for r in clean)
paircosts = [r["pair_cost"] for r in clean if r["pair_cost"]]
upfracs = [r["up_frac"] for r in clean if r["up_frac"] is not None]

print("0xb27b — %d windows (dropped 1 partial-collection window)\n" % n)
print("ACTIVITY / VOLUME:")
print("  trades/window:        avg %.0f   (total %d)" % (sum(r["ntr"] for r in clean) / n, sum(r["ntr"] for r in clean)))
print("  buy $/window:         avg $%.0f  (min $%.0f  max $%.0f)" % (buys / n, min(r["buy"] for r in clean), max(r["buy"] for r in clean)))
print("  Up-share fraction:    avg %.0f%%  (0.50=perfectly balanced; median %.0f%%)" % (100 * sum(upfracs) / len(upfracs), 100 * st.median(upfracs)))
print("\nPAIRING / COST:")
print("  match rate:           avg %.0f%%" % (100 * sum(r["match"] for r in clean) / n))
print("  merged pairs/window:  avg %.0f" % (merg / n))
print("  PAIR COST (buy/merged): avg $%.4f   median $%.4f   (<$1 = merge-profit)" % (sum(paircosts) / len(paircosts), st.median(paircosts)))
print("  windows pair_cost <$1: %d/%d = %.0f%%" % (sum(1 for p in paircosts if p < 1.0), len(paircosts), 100 * sum(1 for p in paircosts if p < 1.0) / len(paircosts)))
print("\nPNL / EDGE:")
print("  net pnl total:        $%+.0f  on $%.0f deployed -> EDGE %+.3f%%" % (sum(pnls), buys, 100 * sum(pnls) / buys))
print("  win rate (pnl>0):     %.0f%%  (%d/%d windows)" % (100 * sum(1 for p in pnls if p > 0) / n, sum(1 for p in pnls if p > 0), n))
print("  pnl/window:           median $%+.1f  best $%+.0f  worst $%+.0f  stdev $%.0f" % (st.median(pnls), max(pnls), min(pnls), st.pstdev(pnls)))
print("  returns source:       merge %.0f%% / redeem %.0f%%" % (100 * merg / (merg + red), 100 * red / (merg + red)))

# PRECISE pair cost = avg Up buy price + avg Down buy price (the true cost of a matched pair
# for a ~50/50 balanced trader). <$1 => genuine window profit; ~$1 => breakeven merge.
pc = [(r["up_avg"] + r["dn_avg"]) for r in clean if r["up_avg"] and r["dn_avg"]]
below = [p for p in pc if p < 1.0]
print("\nPRECISE PAIR COST (avg_up_price + avg_down_price):")
print("  mean $%.4f   median $%.4f   min $%.4f   max $%.4f" % (sum(pc) / len(pc), st.median(pc), min(pc), max(pc)))
print("  windows <$1:          %d/%d = %.0f%%" % (len(below), len(pc), 100 * len(below) / len(pc)))
print("  implied merge profit: $%+.4f/pair  ->  $%+.1f over %d windows (@ avg %.0f pairs/win)"
      % (1.0 - sum(pc) / len(pc), (1.0 - sum(pc) / len(pc)) * merg, len(clean), merg / len(clean)))
print("\n  NOTE: window PnL total was $%+.0f. If precise pair cost <$1, windows ARE the edge;" % sum(pnls))
print("  if ~$1, the +PnL is residual-variance/rebates. 7.5h is a small, noisy sample.")
