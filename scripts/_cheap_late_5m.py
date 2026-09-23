"""Test the guru's apparent main edge on the BIG cache (4500 win / 15.7d), not 5h:
'a side that is CHEAP late in a 5m window is OVERSOLD (panic overshoot) -> wins more
than its price implies.' For each window at minute M, if a side's price < thr, 'buy' it
at that price, hold to resolution; measure hit-rate vs price (edge) + fee sensitivity +
weekly stability. Contrast with the FAVORITE-late buy. Read-only, reuses path cache.
"""
import os, json, collections, datetime as dt

PCACHE = "/tmp/poly_path5_cache"
wins = []
for fn in os.listdir(PCACHE):
    try:
        d = json.load(open(os.path.join(PCACHE, fn)))
    except Exception:
        d = None
    if d and d.get("up") and d.get("winner") and d.get("ts") and len(d["up"]) > 4:
        wins.append(d)
wins.sort(key=lambda w: w["ts"])
print("cached 5m windows: %d over %.1f days\n" % (len(wins), (wins[-1]["ts"] - wins[0]["ts"]) / 86400))


def won(w, side):  # side "Up"/"Dn"
    return (w["winner"] == "Up") == (side == "Up")


def cheap_edge(minute, thr, fee=0.0):
    """Buy any side priced < thr at `minute`, at that price+fee, hold. Edge = hit - price."""
    n = wins_ = 0; spent = payout = 0.0
    for w in wins:
        up = w["up"][minute]
        for side, p in (("Up", up), ("Dn", 1 - up)):
            if p < thr:
                n += 1; cost = p + fee
                spent += cost
                if won(w, side):
                    wins_ += 1; payout += 1.0
    if not n:
        return None
    return {"n": n, "hit": wins_ / n, "avgp": (spent - fee * n) / n,
            "edge": wins_ / n - (spent - fee * n) / n,
            "pct": 100 * (payout - spent) / spent, "pnl": payout - spent}


def fav_edge(minute, thr, fee=0.0):
    """Buy any side priced > thr (favorite) at `minute`, hold."""
    n = wins_ = 0; spent = payout = 0.0
    for w in wins:
        up = w["up"][minute]
        for side, p in (("Up", up), ("Dn", 1 - up)):
            if p > thr:
                n += 1; spent += p + fee
                if won(w, side):
                    wins_ += 1; payout += 1.0
    if not n:
        return None
    return {"n": n, "hit": wins_ / n, "pct": 100 * (payout - spent) / spent}


print("=== CHEAP side (oversold?) at minute 4, threshold sweep, 0 fee ===")
print(" thr    n     hit%%   avg-price  edge(hit-price)   %%ofspend")
for thr in (0.15, 0.25, 0.35):
    r = cheap_edge(4, thr)
    if r:
        print("  <%.2f  %5d  %5.1f%%   %.3f      %+.3f          %+.2f%%" %
              (thr, r["n"], 100 * r["hit"], r["avgp"], r["edge"], r["pct"]))

print("\n=== CHEAP <0.35 at minute 4: fee/spread sensitivity (maker vs taker) ===")
for fee in (0.0, 0.01, 0.02, 0.03):
    r = cheap_edge(4, 0.35, fee)
    print("  entry cost +%.0f cent: %+.2f%% of spend  %s" %
          (fee * 100, r["pct"], "+EV" if r["pct"] > 0 else "-EV"))

print("\n=== compare: FAVORITE >0.55 at minute 4 (0 fee) ===")
r = fav_edge(4, 0.55)
print("  n=%d  hit=%.1f%%  %+.2f%% of spend" % (r["n"], 100 * r["hit"], r["pct"]))

print("\n=== CHEAP <0.35 minute 4: weekly stability (overfit / variance guard) ===")
t0 = wins[0]["ts"]; wk = collections.defaultdict(lambda: [0.0, 0.0, 0])
for w in wins:
    up = w["up"][4]
    for side, p in (("Up", up), ("Dn", 1 - up)):
        if p < 0.35:
            k = (w["ts"] - t0) // (7 * 86400)
            wk[k][0] += (1.0 if won(w, side) else 0.0) - p
            wk[k][1] += p; wk[k][2] += 1
for k in sorted(wk):
    d0 = dt.datetime.utcfromtimestamp(t0 + k * 7 * 86400).strftime("%m-%d")
    print("  week %d (from %s): %+.2f%% of spend   n=%d" %
          (k + 1, d0, 100 * wk[k][0] / wk[k][1] if wk[k][1] else 0, wk[k][2]))
