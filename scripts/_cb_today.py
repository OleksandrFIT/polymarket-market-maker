"""Would OUR circuit-breaker (RegimeTracker) have caught today's afternoon chop?

Runs the REAL RegimeTracker over today's windows in resolution order. Per window the
"directional bet" = the heavy side the wallet built, entry = heavy-side avg price,
winner = on-chain (gamma). Tracks enabled/disabled state + rolling paper-EV. Buckets
the directional PnL into CB-ENABLED (we'd take it) vs CB-PAUSED (we'd skip it) to
quantify how much of the -$831 afternoon the CB would have protected. Read-only.
"""
import urllib.request, json, time, collections, datetime as dt
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quoter.runner.regime_tracker import RegimeTracker

WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
UA = {"User-Agent": "Mozilla/5.0"}
NOW = int(time.time())
SINCE = NOW - 30 * 3600
KYIV = 3 * 3600


def get(u, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.4)


trades, off = [], 0
while True:
    b = get("https://data-api.polymarket.com/trades?user=%s&limit=500&offset=%d" % (WALLET, off))
    if not b:
        break
    keep = [t for t in b if t.get("timestamp", 0) >= SINCE and t.get("side") == "BUY"]
    trades += keep
    if len([t for t in b if t.get("timestamp", 0) >= SINCE]) < len(b) or len(b) < 500:
        break
    off += 500
    time.sleep(0.1)

W = collections.defaultdict(lambda: {"ts": 0, "slug": "", "up_sh": 0.0, "up_usd": 0.0,
                                     "dn_sh": 0.0, "dn_usd": 0.0})
for t in trades:
    w = W[t["conditionId"]]
    w["slug"] = t.get("slug", "")
    try:
        w["ts"] = int(t["slug"].split("-")[-1])
    except Exception:
        pass
    sz, px = float(t["size"]), float(t["price"])
    if t.get("outcomeIndex") == 0:
        w["up_sh"] += sz; w["up_usd"] += sz * px
    else:
        w["dn_sh"] += sz; w["dn_usd"] += sz * px


def winner(slug):
    r = get("https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % slug)
    if not isinstance(r, list) or not r:
        return None
    op = r[0].get("outcomePrices")
    if isinstance(op, str):
        try:
            op = json.loads(op)
        except Exception:
            return None
    if not op or len(op) < 2:
        return None
    if float(op[0]) >= 0.99:
        return "Up"
    if float(op[1]) >= 0.99:
        return "Dn"
    return None


rows = []
for c, w in W.items():
    if not w["ts"]:
        continue
    tf = 300 if "-5m-" in w["slug"] else 900
    win = winner(w["slug"])
    if win is None:
        continue
    up_avg = w["up_usd"] / w["up_sh"] if w["up_sh"] else 0
    dn_avg = w["dn_usd"] / w["dn_sh"] if w["dn_sh"] else 0
    heavy = "Up" if w["up_sh"] >= w["dn_sh"] else "Dn"
    heavy_avg = up_avg if heavy == "Up" else dn_avg
    spent = w["up_usd"] + w["dn_usd"]
    win_sh = w["up_sh"] if win == "Up" else w["dn_sh"]
    rows.append({"res_ts": w["ts"] + tf, "ts": w["ts"], "pnl": win_sh - spent,
                 "heavy": heavy, "heavy_avg": heavy_avg, "win": win})
rows.sort(key=lambda r: r["res_ts"])

# run OUR real RegimeTracker (live config: window=30, min_samples=12, min_ev=0.0)
rt = RegimeTracker(window=30, min_samples=12, min_ev=0.0, fee=0.02)
taken = collections.defaultdict(float)    # hour -> pnl while CB ENABLED
skipped = collections.defaultdict(float)  # hour -> pnl while CB PAUSED
n_taken = n_skip = 0
timeline = []
for r in rows:
    enabled = rt.directional_enabled()
    hr = (r["res_ts"] + KYIV) // 3600 % 24
    if enabled:
        taken[hr] += r["pnl"]; n_taken += 1
    else:
        skipped[hr] += r["pnl"]; n_skip += 1
    timeline.append((hr, enabled, rt.paper_ev()))
    rt.record("Up" if r["heavy"] == "Up" else "Down", r["heavy_avg"],
              "Up" if r["win"] == "Up" else "Down")

print("windows: %d | CB enabled (we'd trade dir): %d | CB paused: %d\n" % (len(rows), n_taken, n_skip))
print("=== directional PnL by Kyiv hour: CB-ENABLED vs CB-PAUSED ===")
print(" hour   taken(we trade)   skipped(CB off)")
allh = sorted(set(list(taken) + list(skipped)))
tt = ss = 0.0
for h in allh:
    tt += taken[h]; ss += skipped[h]
    print("  %02d:00   $%+8.1f        $%+8.1f" % (h, taken[h], skipped[h]))
print("  -----------------------------------------")
print("  TOTAL   $%+8.1f        $%+8.1f" % (tt, ss))
print("\nINTERPRETATION:")
print("  'taken'  = PnL of windows our CB would have ALLOWED directional (our realized).")
print("  'skipped'= PnL of windows our CB would have PAUSED (loss AVOIDED if negative).")
print("  If skipped<<0 in the afternoon, the CB protected us from that bleed.")

# show the paper-EV timeline transitions (enable<->disable)
print("\n=== CB state transitions (paper-EV crossing) ===")
prev = None
for hr, en, ev in timeline:
    if en != prev:
        print("  Kyiv ~%02d:00  CB -> %s   (paper_ev %s)" %
              (hr, "ENABLED" if en else "PAUSED", "%.3f" % ev if ev is not None else "warmup"))
        prev = en
