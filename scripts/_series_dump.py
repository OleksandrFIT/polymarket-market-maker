"""Emit per-window series (our sim vs 0xb27b) as JSON for charting. Run: _series_dump.py <his> <sim>"""
import sys
import re
import json
import collections

HIS = sys.argv[1]
SIM = sys.argv[2]

sim = {}
for line in open(SIM):
    d = re.search(r"\[(\d+) done\] win=(\w+) merged=([\d.]+) pair=([\d.na/]+) "
                  r"resid=(\w+?)(\d+)\((\w+|-)\) PnL=\$([-+][\d.]+)", line)
    if d:
        try:
            pv = float(d.group(4))
        except ValueError:
            pv = None
        sim[int(d.group(1))] = dict(win=d.group(2), merged=float(d.group(3)),
                                    pair=pv, ro=d.group(7), pnl=float(d.group(8)))

per = collections.defaultdict(lambda: dict(bU=[], bD=[], merge=0.0, redeem=0.0, ntr=0))
for line in open(HIS):
    try:
        x = json.loads(line)
    except (ValueError, TypeError):
        continue
    sl = x.get("slug", "")
    if not sl.startswith("btc-updown-5m-"):
        continue
    ts0 = int(sl.rsplit("-", 1)[1])
    p = per[ts0]
    t = x["type"]
    if t == "TRADE":
        p["ntr"] += 1
        if x["side"] == "BUY":
            (p["bU"] if x["outcome"] == "Up" else p["bD"]).append((float(x["price"]), float(x["size"])))
    elif t == "MERGE":
        p["merge"] += float(x["usdcSize"])
    elif t == "REDEEM":
        p["redeem"] += float(x["usdcSize"])

out = []
for ts0 in sorted(sim):
    s = sim[ts0]
    p = per.get(ts0)
    row = dict(ts=ts0, our_pnl=s["pnl"], our_pair=s["pair"], our_resid=s["ro"], our_merged=s["merged"])
    if p and (p["bU"] or p["bD"]):
        shU = sum(z for _, z in p["bU"])
        shD = sum(z for _, z in p["bD"])
        cU = sum(pr * z for pr, z in p["bU"])
        cD = sum(pr * z for pr, z in p["bD"])
        vU = cU / shU if shU else 0
        vD = cD / shD if shD else 0
        naked = abs(shU - shD)
        row.update(his_pnl=round(p["merge"] + p["redeem"] - cU - cD, 1),
                   his_pair=round(vU + vD, 3), his_trades=p["ntr"],
                   his_buys=round(cU + cD, 0),
                   his_mn=round(p["merge"] / naked, 1) if naked >= 1 else None,
                   pending=(p["redeem"] == 0))
    out.append(row)
print(json.dumps(out))
