"""Merge-maker backtest on REAL 15-minute BTC market tapes — does our pair
strategy work on 15m where it failed on 5m? (the guru runs 15m, +$22.5k).

Measures, per real 15m window: the lowest price each side was actually SOLD at
(size>=5) = where our maker bid could fill -> achievable pair cost = min_up+min_dn.
If < $1 a locked arb pair exists. Reports % achievable, avg pair cost, edge, and
a simple merge PnL (assemble pair when <$1 else sit).
Read-only; separate cache to avoid 5m/15m ts collisions.
"""
import urllib.request, json, os, time, statistics

UA = {"User-Agent": "Mozilla/5.0"}
CACHE = "/tmp/poly_tape15_cache"
os.makedirs(CACHE, exist_ok=True)
SIZE_MIN = 5
END_TS = 1781619300          # a recent resolved 15m window (ts % 900 == 0)
STEP = 900
N = 130


def _get(url, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.8)


def meta(ts):
    r = _get("https://gamma-api.polymarket.com/markets?slug=btc-updown-15m-%d&closed=true" % ts)
    if not isinstance(r, list) or not r:
        return None
    cid = r[0].get("conditionId")
    return cid if cid else None


def tape(ts, cid):
    fn = os.path.join(CACHE, "%d.json" % ts)
    if os.path.exists(fn):
        return json.load(open(fn))
    all_tr = []; off = 0
    while True:
        tr = _get("https://data-api.polymarket.com/trades?market=%s&limit=500&offset=%d" % (cid, off))
        if not isinstance(tr, list) or not tr:
            break
        all_tr += tr; off += len(tr)
        if len(tr) < 500 or off > 8000:
            break
    win = [(int(t["timestamp"]), t["outcome"], t["side"], float(t["price"]), float(t["size"]))
           for t in all_tr if ts <= int(t["timestamp"]) < ts + STEP]
    win.sort()
    json.dump(win, open(fn, "w"))
    return win


def winner(ts):
    # 15m window: use 3x 5m klines, compare close at +15m vs open
    k = _get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&startTime=%d&limit=3" % (ts*1000))
    if len(k) < 3:
        return None
    return "Up" if float(k[-1][4]) >= float(k[0][1]) else "Down"


rows = []
ts = END_TS
miss = 0
while len(rows) < N and miss < 40:
    cid = None
    try:
        cid = meta(ts)
    except Exception:
        cid = None
    if cid:
        try:
            tw = tape(ts, cid)
            w = winner(ts)
            if tw and w:
                # collect SELL trades per side (price, size) for depth-based fills
                sells = {"Up": [], "Down": []}
                for (t, out, side, px, sz) in tw:
                    if side == "SELL" and sz >= 1:
                        sells[out].append((px, sz))
                rows.append({"ts": ts, "sells": sells, "win": w})
            else:
                miss += 1
        except Exception:
            miss += 1
    else:
        miss += 1
    ts -= STEP
    time.sleep(0.04)

n = len(rows)
print("=== 15m MERGE BACKTEST (depth-based fills) — %d real windows ===" % n)
print("our bid fills 5 sh once >= DEPTH sh of sells printed at/below our level (queue proxy)")
print()


def fill_price(side_sells, depth):
    """lowest price at which cumulative SELL volume reaches `depth` (queue+size)."""
    s = sorted(side_sells)            # ascending by price
    cum = 0.0
    for px, sz in s:
        cum += sz
        if cum >= depth:
            return px
    return None                       # never enough depth -> can't fill cheap


print("%-8s %8s %9s %10s %9s %9s" % ("DEPTH", "pair<$1%", "avgpair", "edge/pair", "$/win5sh", "guru?"))
for depth in (5, 20, 50, 100, 200):
    costs = []
    for r in rows:
        fu = fill_price(r["sells"]["Up"], depth)
        fd = fill_price(r["sells"]["Down"], depth)
        if fu is not None and fd is not None:
            costs.append(fu + fd)
    if not costs:
        continue
    under = [c for c in costs if c < 1.0]
    # merge PnL: pair when <$1 else sit (0); 5-share
    blend = [5*(1.0 - c) if c < 1.0 else 0.0 for c in costs] + [0.0]*(n-len(costs))
    avgpair = statistics.mean(under) if under else 0
    tag = "<-- ~guru" if 0.88 <= avgpair <= 0.96 else ""
    print("%-8d %7.0f%% %9.3f %10.3f %+9.2f %9s" %
          (depth, 100*len(under)/len(costs), avgpair, 1-avgpair if under else 0,
           sum(blend)/n, tag))
print()
print("guru REAL pair = $0.91 (+$0.09/pair). Find the DEPTH that matches him = our")
print("realistic level. Smaller size (us) needs less depth -> can fill cheaper than guru.")
print("compare 5m pure ladder real = −$0.51/win.")
