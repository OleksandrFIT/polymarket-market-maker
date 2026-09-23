"""Where does the guru's money come from? Split each 5m BUY into FAVORITE (price>0.55),
MID (0.35-0.55), CHEAP (<0.35), by ENTRY TIMING (early <2.5min / late >=2.5min), resolve
the window, and compute realized PnL per bucket. Answers: is it the late-favorite momentum
buy or the late-cheap longshot that pays? Tactic forensics, volume ignored.
Usage: python3 scripts/_forensic_pnl_split.py <address> [hours]
"""
import urllib.request, json, time, sys, collections

ADDR = sys.argv[1] if len(sys.argv) > 1 else "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
HOURS = float(sys.argv[2]) if len(sys.argv) > 2 else 6
BASE = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0"}
SINCE = int(time.time()) - HOURS * 3600
_wcache = {}


def get(u, tries=4):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.6)


def winner_side(slug):  # 0=Up wins, 1=Dn wins, None=unknown
    if slug in _wcache:
        return _wcache[slug]
    r = get("https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % slug)
    v = None
    if isinstance(r, list) and r:
        op = r[0].get("outcomePrices")
        if isinstance(op, str):
            try: op = json.loads(op)
            except Exception: op = None
        if op and len(op) >= 2:
            if float(op[0]) >= 0.99: v = 0
            elif float(op[1]) >= 0.99: v = 1
    _wcache[slug] = v
    return v


trades, off = [], 0
while True:
    b = get("%s/trades?user=%s&limit=500&offset=%d" % (BASE, ADDR, off))
    if not isinstance(b, list) or not b:
        break
    trades += [t for t in b if t.get("timestamp", 0) >= SINCE and "-5m-" in (t.get("slug") or "")]
    if min((t.get("timestamp", 0) for t in b), default=0) < SINCE or len(b) < 500:
        break
    off += 500; time.sleep(0.15)

print("wallet %s  5m BUYs last %.1fh (resolving windows)\n" % (ADDR[:12], HOURS))

# bucket: (price_class, timing) -> [shares, cost, payout]
buckets = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
seen_slugs = set()
for t in trades:
    if t.get("side") != "BUY":
        continue
    slug = t.get("slug") or ""
    try:
        open_ts = int(slug.rsplit("-", 1)[1])
    except Exception:
        continue
    w = winner_side(slug)
    if w is None:
        continue
    seen_slugs.add(slug)
    oi = t.get("outcomeIndex")
    if oi not in (0, 1):
        continue
    price = float(t.get("price", 0)); size = float(t.get("size", 0))
    minute = (t.get("timestamp", 0) - open_ts) / 60.0
    pcls = "FAV>.55" if price > 0.55 else ("MID.35-.55" if price >= 0.35 else "CHEAP<.35")
    tim = "late>=2.5m" if minute >= 2.5 else "early<2.5m"
    payout = size if oi == w else 0.0
    b = buckets[(pcls, tim)]
    b[0] += size; b[1] += size * price; b[2] += payout

print("resolved windows: %d\n" % len(seen_slugs))
print("%-12s %-11s %8s %9s %9s %8s" % ("price", "timing", "shares", "cost$", "payout$", "PnL$"))
tot = [0.0, 0.0, 0.0]
order = ["FAV>.55", "MID.35-.55", "CHEAP<.35"]
for pcls in order:
    for tim in ("early<2.5m", "late>=2.5m"):
        b = buckets.get((pcls, tim))
        if not b:
            continue
        pnl = b[2] - b[1]
        tot[0] += b[0]; tot[1] += b[1]; tot[2] += b[2]
        print("%-12s %-11s %8.0f %9.1f %9.1f %+8.1f  %s" %
              (pcls, tim, b[0], b[1], b[2], pnl, "WIN" if pnl > 0 else "LOSS"))
print("-" * 62)
print("%-24s %8.0f %9.1f %9.1f %+8.1f  (%.1f%% of cost)" %
      ("TOTAL", tot[0], tot[1], tot[2], tot[2] - tot[1],
       100 * (tot[2] - tot[1]) / tot[1] if tot[1] else 0))
