"""Dump ALL structured data for one 0xb27b BTC-5m window as compact JSON (for charting):
price histogram of his buys by side, cumulative Up/Down/merge timeline, totals.
Run: python3 scripts/_window_dump.py <his.jsonl> <window_ts0>"""
import sys
import json
import collections

HIS = sys.argv[1]
TS0 = int(sys.argv[2])
slug = "btc-updown-5m-%d" % TS0

buys, merges, redeem = [], [], 0.0
for line in open(HIS):
    try:
        x = json.loads(line)
    except (ValueError, TypeError):
        continue
    if x.get("slug") != slug:
        continue
    rel = int(x["timestamp"]) - TS0
    t = x["type"]
    if t == "TRADE" and x["side"] == "BUY":
        buys.append((rel, x["outcome"], float(x["price"]), float(x["size"])))
    elif t == "MERGE":
        merges.append((rel, float(x["usdcSize"])))
    elif t == "REDEEM":
        redeem += float(x["usdcSize"])
buys.sort()
merges.sort()

byside = {"Up": {"sh": 0.0, "cost": 0.0}, "Down": {"sh": 0.0, "cost": 0.0}}
bands = ["0.0-0.1", "0.1-0.2", "0.2-0.3", "0.3-0.4", "0.4-0.5",
         "0.5-0.6", "0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0"]
hist = {"Up": [0.0] * 10, "Down": [0.0] * 10}
BUCK = 15
tl = collections.defaultdict(lambda: {"Up": 0.0, "Down": 0.0, "m": 0.0})
for rel, side, pr, sz in buys:
    byside[side]["sh"] += sz
    byside[side]["cost"] += pr * sz
    hist[side][min(int(pr * 10), 9)] += sz
    tl[(rel // BUCK) * BUCK]["Up" if side == "Up" else "Down"] += sz
for rel, usd in merges:
    tl[(rel // BUCK) * BUCK]["m"] += usd

timeline = []
cumU = cumD = cumM = 0.0
for b in sorted(tl):
    cumU += tl[b]["Up"]
    cumD += tl[b]["Down"]
    cumM += tl[b]["m"]
    timeline.append({"t": b, "cumUp": round(cumU, 1), "cumDn": round(cumD, 1),
                     "cumMerge": round(cumM, 1)})

vU = byside["Up"]["cost"] / byside["Up"]["sh"] if byside["Up"]["sh"] else 0
vD = byside["Down"]["cost"] / byside["Down"]["sh"] if byside["Down"]["sh"] else 0
tot_buy = byside["Up"]["cost"] + byside["Down"]["cost"]
tot_merge = sum(u for _, u in merges)
naked = abs(byside["Up"]["sh"] - byside["Down"]["sh"])
out = {
    "slug": slug, "ts0": TS0,
    "n_trades": len(buys), "n_merges": len(merges),
    "buys_usd": round(tot_buy, 1), "merge_usd": round(tot_merge, 1), "redeem_usd": round(redeem, 1),
    "pnl": round(tot_merge + redeem - tot_buy, 1),
    "up": {"sh": round(byside["Up"]["sh"], 1), "vwap": round(vU, 3)},
    "dn": {"sh": round(byside["Down"]["sh"], 1), "vwap": round(vD, 3)},
    "pair_vwap": round(vU + vD, 3),
    "match_naked": round(tot_merge / naked, 1) if naked >= 1 else None,
    "bands": bands, "hist": hist, "timeline": timeline,
}
print(json.dumps(out))
