"""Side-by-side: our hybrid PAPER sim vs 0xb27b's REAL trades on the SAME BTC-5m windows.
His pair/match:naked/trades need only buys+merges (reliable immediately); his PnL needs REDEEM
(lags a few min after a window resolves -> flagged 'redeem pending').
Run: python3 scripts/_comp_vs_sim.py <his.jsonl> <sim.out>"""
import sys
import re
import json
import collections

HIS = sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/comp_today.jsonl"
SIM = sys.argv[2] if len(sys.argv) > 2 else "/home/ubuntu/hybridsim.out"

sim = {}
for line in open(SIM):
    d = re.search(r"\[(\d+) done\] win=(\w+) merged=([\d.]+) pair=([\d.na/]+) "
                  r"resid=(\w+?)(\d+)\((\w+|-)\) PnL=\$([-+][\d.]+)", line)
    if d:
        sim[int(d.group(1))] = dict(win=d.group(2), merged=float(d.group(3)), pair=d.group(4),
                                    ro=d.group(7), pnl=float(d.group(8)))

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

print("SHARED WINDOWS — our hybrid paper sim  vs  0xb27b real")
print("%-11s | OUR sim: pair/merged/PnL        | 0xb27b: trades/pair/match:naked/buys/PnL" % "window")
for ts0 in sorted(sim):
    s = sim[ts0]
    p = per.get(ts0)
    ours = "pair=%s merged=%.0f pnl=$%+.2f resid=%s" % (s["pair"], s["merged"], s["pnl"], s["ro"])
    if not p or (not p["bU"] and not p["bD"]):
        print("%d | %-30s | (no his data collected for this window)" % (ts0, ours))
        continue
    shU = sum(z for _, z in p["bU"])
    shD = sum(z for _, z in p["bD"])
    cU = sum(pr * z for pr, z in p["bU"])
    cD = sum(pr * z for pr, z in p["bD"])
    vU = cU / shU if shU else 0
    vD = cD / shD if shD else 0
    hispair = (vU + vD) if (shU and shD) else 0
    naked = abs(shU - shD)
    mn = (p["merge"] / naked) if naked >= 1 else 999.0
    hispnl = p["merge"] + p["redeem"] - cU - cD
    pend = " [redeem PENDING]" if p["redeem"] == 0 else ""
    print("%d | %-30s | trades=%d pair=%.3f m:n=%.1f buys=$%.0f pnl=$%+.2f%s" % (
        ts0, ours, p["ntr"], hispair, mn, cU + cD, hispnl, pend))
