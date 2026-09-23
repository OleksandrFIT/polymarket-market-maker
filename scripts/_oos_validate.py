"""Is the missing edge SELECTION (which 5m windows to trade), not sub-minute execution?
Reuses cached 5m paths. Policy: early two-sided + lean into mover (as before), but only
in SELECTED windows (leader consistent min1==min2, entry in a band). Reports edge on the
selected subset, fraction traded, weekly stability (overfit guard), fee sensitivity.
Read-only, no network.
"""
import os, json, statistics, collections, datetime as dt

PCACHE = "/tmp/poly_path5_cache"
EARLY = (0, 1, 2)
LEAN = 3

wins = []
for fn in os.listdir(PCACHE):
    try:
        d = json.load(open(os.path.join(PCACHE, fn)))
    except Exception:
        d = None
    if d and d.get("up") and d.get("winner") and d.get("ts"):
        wins.append(d)
wins.sort(key=lambda w: w["ts"])
print("cached 5m windows: %d over %.1f days\n" % (len(wins), (wins[-1]["ts"] - wins[0]["ts"]) / 86400))


def leader_at(w, m):
    p = w["up"][m]
    return ("Up" if p > 0.5 else "Dn"), (p if p > 0.5 else 1 - p)


def selected(w, lo, hi, consistent):
    l1, _ = leader_at(w, 1); l2, p2 = leader_at(w, 2)
    if consistent and l1 != l2:
        return False
    return lo <= p2 <= hi


def run(sel_lo, sel_hi, consistent, fee=0.0):
    rows = []   # (ts, pnl, spent)
    for w in wins:
        if not selected(w, sel_lo, sel_hi, consistent):
            continue
        up = w["up"]; inv = {"Up": 0.0, "Dn": 0.0}; cost = 0.0
        for m in EARLY:
            p = up[m]; leader = "Up" if p > 0.5 else "Dn"
            lp = (p if leader == "Up" else 1 - p) + fee
            lag = "Dn" if leader == "Up" else "Up"; gp = (1 - (p if leader == "Up" else 1 - p)) + fee
            inv[leader] += LEAN; cost += LEAN * lp
            inv[lag] += 1; cost += gp
        win = "Up" if w["winner"] == "Up" else "Dn"
        rows.append((w["ts"], inv[win] - cost, cost))
    if not rows:
        return None
    pnl = sum(r[1] for r in rows); spent = sum(r[2] for r in rows)
    return {"n": len(rows), "frac": 100 * len(rows) / len(wins), "pct": 100 * pnl / spent,
            "pnl_win": pnl / len(rows), "rows": rows}


print("=== SELECTION scan (lean 3:1, maker 0 spread) ===")
print(" rule                         windows(%%)   %ofspend   PnL/win")
configs = [
    ("all windows",            0.50, 1.00, False),
    ("consistent leader",      0.50, 1.00, True),
    ("consist + band .62-.78", 0.62, 0.78, True),
    ("consist + band .65-.75", 0.65, 0.75, True),
    ("consist + band .60-.72", 0.60, 0.72, True),
]
best = None
for name, lo, hi, con in configs:
    r = run(lo, hi, con)
    if not r:
        continue
    print("  %-26s  %4.0f%%      %+.2f%%    %+.4f" % (name, r["frac"], r["pct"], r["pnl_win"]))
    if best is None or r["pct"] > best[1]:
        best = (name, r["pct"], lo, hi, con)

# weekly stability + fee sensitivity for the best rule
name, pct, lo, hi, con = best
r = run(lo, hi, con)
print("\n=== best rule '%s': weekly stability (overfit guard) ===" % name)
t0 = r["rows"][0][0]
wk = collections.defaultdict(lambda: [0.0, 0.0])
for ts, p, s in r["rows"]:
    k = (ts - t0) // (7 * 86400); wk[k][0] += p; wk[k][1] += s
for k in sorted(wk):
    d0 = dt.datetime.utcfromtimestamp(t0 + k * 7 * 86400).strftime("%m-%d")
    print("  week %d (from %s): %+.2f%% of spend" % (k + 1, d0, 100 * wk[k][0] / wk[k][1] if wk[k][1] else 0))

print("\n=== fee sensitivity for '%s' ===" % name)
for fee in (0.0, 0.005, 0.01, 0.02):
    rf = run(lo, hi, con, fee)
    print("  fee %.1f¢:  %+.2f%% of spend  %s" % (fee * 100, rf["pct"], "+EV" if rf["pct"] > 0 else "-EV"))
