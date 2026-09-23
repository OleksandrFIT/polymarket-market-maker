"""Deep tactic of NEW bot 0xb27b within the recent ~3500 trades (data-api cap).
HOW does it hold ~0.4% edge at huge volume? Look at: intensity (fills/window),
WHEN in the window it trades (minute histogram, 5m vs 15m), maker-vs-taker (fill
price vs contemporaneous mid from clob path), and full sequences of active windows.
Read-only.
"""
import urllib.request, json, time, collections, statistics
ADDR = "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
UA = {"User-Agent": "Mozilla/5.0"}


def get(u, tries=4):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.8)


# fetch up to the 3500 cap
trades, off = [], 0
while off < 3500:
    b = get("https://data-api.polymarket.com/trades?user=%s&limit=500&offset=%d" % (ADDR, off))
    if not isinstance(b, list) or not b:
        break
    trades += b
    if len(b) < 500:
        break
    off += 500; time.sleep(0.15)
trades = [t for t in trades if t.get("side") == "BUY"]
print("recent BUY trades: %d" % len(trades))

def tf_of(s): return 300 if "-5m-" in s else (900 if "-15m-" in s else None)

# group by window
W = collections.defaultdict(lambda: {"slug": "", "open": 0, "tf": 0, "fills": []})
for t in trades:
    s = t.get("slug", ""); tf = tf_of(s)
    if not tf:
        continue
    w = W[t["conditionId"]]; w["slug"] = s; w["tf"] = tf
    try: w["open"] = int(s.split("-")[-1])
    except Exception: pass
    w["fills"].append((t["timestamp"], "Up" if t.get("outcomeIndex") == 0 else "Dn",
                       float(t["size"]), float(t["price"])))

wins = [w for w in W.values() if w["open"]]
fills_per = [len(w["fills"]) for w in wins]
tf5 = [w for w in wins if w["tf"] == 300]; tf15 = [w for w in wins if w["tf"] == 900]
print("windows: %d (5m %d / 15m %d)" % (len(wins), len(tf5), len(tf15)))
print("fills/window: median %d  mean %.1f  max %d" %
      (statistics.median(fills_per), statistics.mean(fills_per), max(fills_per)))

# WHEN in the window (minute-of-window) — separately 5m and 15m
def minute_hist(group, dur):
    h = collections.Counter()
    tot = 0
    for w in group:
        for ts, side, sz, px in w["fills"]:
            m = int((ts - w["open"]) // 60)
            if 0 <= m <= dur // 60:
                h[m] += 1; tot += 1
    return h, tot

for grp, dur, name in ((tf5, 300, "5m"), (tf15, 900, "15m")):
    if not grp:
        continue
    h, tot = minute_hist(grp, dur)
    print("\n=== WHEN it trades within %s window (minute-of-window) ===" % name)
    for m in sorted(h):
        bar = "#" * int(40 * h[m] / max(h.values()))
        print("  min %2d  %5d  %s" % (m, h[m], bar))

# maker/taker estimate on a sample of windows (price vs clob mid at that minute)
print("\n=== maker/taker estimate (sample 40 windows, price vs mid) ===")
import random
random.seed(2)
sample = random.sample(wins, min(40, len(wins)))
mk = tk = mid = 0
for w in sample:
    # up-token mid path
    cond = None
    # need up token id: fetch from gamma
    r = get("https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % w["slug"])
    if not isinstance(r, list) or not r:
        continue
    toks = r[0].get("clobTokenIds")
    if isinstance(toks, str): toks = json.loads(toks)
    if not toks: continue
    up_tok = toks[0]
    h = get("https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=1"
            % (up_tok, w["open"] - 30, w["open"] + w["tf"] + 30))
    hist = h.get("history", []) if isinstance(h, dict) else []
    if not hist: continue
    def upmid(ts):
        best = min(hist, key=lambda p: abs(p["t"] - ts))
        return best["p"] if abs(best["t"] - ts) <= 90 else None
    for ts, side, sz, px in w["fills"]:
        um = upmid(ts)
        if um is None: continue
        mref = um if side == "Up" else 1 - um
        if px < mref - 0.02: mk += 1
        elif px > mref + 0.02: tk += 1
        else: mid += 1
    time.sleep(0.05)
cl = mk + tk + mid
if cl:
    print("  MAKER (buy < mid-2c): %d (%.0f%%) | TAKER (> mid+2c): %d (%.0f%%) | at-mid: %d (%.0f%%)" %
          (mk, 100*mk/cl, tk, 100*tk/cl, mid, 100*mid/cl))

# full sequence of the 2 most active windows
print("\n=== FULL sequence of 2 most active windows ===")
for w in sorted(wins, key=lambda x: -len(x["fills"]))[:2]:
    up = sum(sz for ts, sd, sz, px in w["fills"] if sd == "Up")
    dn = sum(sz for ts, sd, sz, px in w["fills"] if sd == "Dn")
    print("\n  %s  (%d fills, Up %.0f sh / Dn %.0f sh)" % (w["slug"], len(w["fills"]), up, dn))
    for ts, side, sz, px in sorted(w["fills"])[:40]:
        m = (ts - w["open"]) / 60.0
        print("    m%4.1f  %-3s %5.0f @ %.3f" % (m, side, sz, px))
