"""Quick probe: how active is the wallet in the last 24h, and what does a trade
record actually look like (keys)? Read-only."""
import urllib.request, json, time, collections, datetime as dt

WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
BASE = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0"}
NOW = int(time.time())
SINCE = NOW - 24*3600


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))


def page(endpoint, limit=500):
    out, off = [], 0
    while True:
        try:
            b = get("%s/%s?user=%s&limit=%d&offset=%d" % (BASE, endpoint, WALLET, limit, off))
        except Exception as e:
            print("  [%s] error at off=%d: %s" % (endpoint, off, e))
            break
        if not b:
            break
        keep = [x for x in b if x.get("timestamp", 0) >= SINCE]
        out += keep
        if len(keep) < len(b) or len(b) < limit:
            break
        off += limit
        time.sleep(0.15)
    return out


print("now=%d  since=%d (%s UTC)" % (NOW, SINCE, dt.datetime.utcfromtimestamp(SINCE).strftime("%Y-%m-%d %H:%M")))
tr = page("trades")
ac = page("activity", limit=100)
print("trades 24h: %d | activity 24h: %d\n" % (len(tr), len(ac)))

if tr:
    print("TRADE keys:", sorted(tr[0].keys()))
    print("sample trade:", json.dumps(tr[0], indent=2)[:800], "\n")
if ac:
    print("ACTIVITY keys:", sorted(ac[0].keys()))
    print("activity types:", collections.Counter(a.get("type") for a in ac))
    # show one redeem if any
    for a in ac:
        if a.get("type", "").upper() in ("REDEEM", "REDEMPTION"):
            print("sample redeem:", json.dumps(a, indent=2)[:600]); break

# asset / market breakdown
asset = collections.Counter()
for t in tr:
    ti = t.get("title", "")
    a = ti.split()[0] if ti else "?"
    asset[a] += 1
print("\ntrades by leading title word:", dict(asset.most_common(12)))
side = collections.Counter(t.get("side") for t in tr)
print("sides:", dict(side))
print("distinct titles (markets):", len(set(t.get("title") for t in tr)))
