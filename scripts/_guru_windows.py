"""Compact per-window summary of the guru's trades + outcome + the breakeven-hedge check.
Tests the rule: favorite_shares ~= total_spent  => 'favorite wins' ~= breakeven,
and the rest piled on the cheap underdog = reversal lottery.
"""
import urllib.request, json, time, collections

UA = {"User-Agent": "Mozilla/5.0"}
ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"


def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))


rows, off = [], 0
while off < 1500:
    r = get("https://data-api.polymarket.com/activity?user=%s&limit=500&offset=%d&type=TRADE" % (ADDR, off))
    if not isinstance(r, list) or not r:
        break
    rows += r; off += len(r)
    if len(r) < 500:
        break

W = collections.defaultdict(lambda: {"Up": [0.0, 0.0], "Down": [0.0, 0.0], "ts": 0})
for t in rows:
    slug = t.get("slug") or ""
    if "btc-updown-15m" not in slug or t.get("side") != "BUY":
        continue
    outc = t.get("outcome")
    if outc not in ("Up", "Down"):
        continue
    w = W[slug]
    w[outc][0] += float(t.get("size") or 0)
    w[outc][1] += float(t.get("price") or 0) * float(t.get("size") or 0)
    w["ts"] = int(slug.split("-")[-1])

now = time.time()
winner_cache = {}
def winner(slug, ts):
    if ts + 900 > now - 30:
        return None  # not resolved yet
    if slug in winner_cache:
        return winner_cache[slug]
    try:
        m = get("https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % slug)
        op = m[0].get("outcomePrices")
        if isinstance(op, str):
            op = json.loads(op)
        w = "Up" if float(op[0]) > 0.5 else "Down"
    except Exception:
        w = None
    winner_cache[slug] = w
    return w

print("UTC now %s   guru windows (newest last):\n" % time.strftime("%H:%M:%S", time.gmtime(now)))
for slug in sorted(W, key=lambda s: W[s]["ts"]):
    w = W[slug]; ts = w["ts"]
    uq, uc = w["Up"]; dq, dc = w["Down"]
    if uq + dq < 5:
        continue
    ua = uc / uq if uq else 0; da = dc / dq if dq else 0
    spent = uc + dc
    fav = "Up" if ua >= da else "Down"
    favq = uq if fav == "Up" else dq
    tilt = "Up" if uq > dq else "Down"
    up_pnl = uq - spent
    dn_pnl = dq - spent
    wn = winner(slug, ts)
    real = ("" if wn is None else "  WINNER=%s PnL=%+.0f" % (wn, (uq if wn == "Up" else dq) - spent))
    hhmm = time.strftime("%H:%M", time.gmtime(ts))
    print("%s w%s | Up %4.0f@%.2f  Down %4.0f@%.2f  $%-5.0f | fav=%-4s(≈$%.0f? %.0f) tilt=%-4s | Up→%+.0f Down→%+.0f%s"
          % (hhmm, slug[-4:], uq, ua, dq, da, spent, fav, spent, favq, tilt, up_pnl, dn_pnl, real))
