"""Pull ALL /activity for a wallet on a UTC day via start/end time-slicing (bypasses the
offset-3500 truncation). Half-hour slices keep each fetch under the 500 cap. Saves JSONL + report.
Run: python3 scripts/_k2_dump.py <addr> <YYYY-MM-DD> <out.jsonl>"""
import sys
import json
import time
import datetime
import collections
import urllib.request

ADDR = sys.argv[1]
DAY = sys.argv[2]
OUT = sys.argv[3]
UA = {"User-Agent": "Mozilla/5.0"}
y, m, d = [int(x) for x in DAY.split("-")]
LO = int(datetime.datetime(y, m, d, tzinfo=datetime.UTC).timestamp())
HI = LO + 86400
SLICE = 1800                                     # 30-min slices


def get(start, end):
    u = ("https://data-api.polymarket.com/activity?user=%s&limit=500&start=%d&end=%d"
         % (ADDR, start, end))
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25))


def key(x):
    return (x.get("transactionHash", ""), x.get("type", ""), x.get("asset", ""),
            round(float(x.get("usdcSize", 0)), 4), round(float(x.get("size", 0)), 4))


seen = set()
rows = []
s = LO
maxed = 0
while s < HI:
    e = min(s + SLICE, HI)
    try:
        r = get(s, e)
    except Exception as ex:
        print("slice %d-%d ERR %s" % (s, e, ex))
        r = []
    if len(r) >= 500:
        maxed += 1                               # slice may be truncated (should not happen at 30m)
    for x in r:
        k = key(x)
        if k in seen:
            continue
        seen.add(k)
        rows.append(x)
    s = e
    time.sleep(0.2)

rows.sort(key=lambda x: int(x["timestamp"]))
with open(OUT, "w") as f:
    for x in rows:
        f.write(json.dumps(x) + "\n")

fam = collections.Counter()
typ = collections.Counter()
side = collections.Counter()
tmin = tmax = None
for x in rows:
    sl = x.get("slug", "")
    fam[sl.rsplit("-", 2)[0] if "updown" in sl else (sl.split("-")[0] if sl else "?")] += 1
    typ[x["type"]] += 1
    if x["type"] == "TRADE":
        side[x.get("side", "?")] += 1
    t = int(x["timestamp"])
    tmin = t if tmin is None else min(tmin, t)
    tmax = t if tmax is None else max(tmax, t)


def fmt(t):
    return datetime.datetime.fromtimestamp(t, datetime.UTC).strftime("%H:%M") if t else "-"


print("wrote %d events for %s -> %s" % (len(rows), DAY, OUT))
print("coverage: %s -> %s UTC  (slices possibly truncated: %d)" % (fmt(tmin), fmt(tmax), maxed))
print("types:", dict(typ), "| trade sides:", dict(side))
print("markets:", dict(fam.most_common(6)))
