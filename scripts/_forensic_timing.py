"""Reconstruct a wallet's PER-WINDOW 5m tactic with in-window TIMING.
For each 5m window: sort its trades by minute-since-open, show side (Up/Dn),
price, size. Characterize: entry minute, favorite vs cheap, one/two-sided,
accumulate vs one-shot, late-longshot buys. Tactic only (volume ignored).
Usage: python3 scripts/_forensic_timing.py <address> [hours]
"""
import urllib.request, json, time, sys, collections, statistics

ADDR = sys.argv[1] if len(sys.argv) > 1 else "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
HOURS = float(sys.argv[2]) if len(sys.argv) > 2 else 4
BASE = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0"}
NOW = int(time.time()); SINCE = NOW - HOURS * 3600


def get(u, tries=4):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.6)


trades, off = [], 0
while True:
    b = get("%s/trades?user=%s&limit=500&offset=%d" % (BASE, ADDR, off))
    if not isinstance(b, list) or not b:
        break
    keep = [t for t in b if t.get("timestamp", 0) >= SINCE and "-5m-" in (t.get("slug") or "")]
    trades += keep
    if min((t.get("timestamp", 0) for t in b), default=0) < SINCE or len(b) < 500:
        break
    off += 500; time.sleep(0.15)

print("wallet %s  5m trades last %.1fh: %d\n" % (ADDR[:12], HOURS, len(trades)))
if not trades:
    print("no 5m trades"); sys.exit()

# group by window
byw = collections.defaultdict(list)
for t in trades:
    slug = t.get("slug") or ""
    try:
        open_ts = int(slug.rsplit("-", 1)[1])
    except Exception:
        continue
    minute = (t.get("timestamp", 0) - open_ts) / 60.0
    byw[slug].append({
        "min": minute,
        "side": "Up" if t.get("outcomeIndex") == 0 else "Dn",
        "act": t.get("side"),                      # BUY / SELL
        "price": float(t.get("price", 0)),
        "size": float(t.get("size", 0)),
    })

# aggregate tactic stats
first_min, buy_prices, two_sided, sells = [], [], 0, 0
side_first_price = []      # price of FIRST buy in each window (favorite vs cheap)
late_cheap = 0             # buys < 0.15 in last 90s
accumulates = 0            # >3 buys same side
n = len(byw)
for slug, ts in byw.items():
    ts.sort(key=lambda x: x["min"])
    buys = [x for x in ts if x["act"] == "BUY"]
    if not buys:
        continue
    first_min.append(buys[0]["min"])
    side_first_price.append(buys[0]["price"])
    for x in buys:
        buy_prices.append(x["price"])
    ups = sum(1 for x in buys if x["side"] == "Up"); dns = len(buys) - ups
    if ups > 0 and dns > 0:
        two_sided += 1
    if any(x["act"] == "SELL" for x in ts):
        sells += 1
    if any(x["price"] < 0.15 and x["min"] > 3.5 for x in buys):
        late_cheap += 1
    for side in ("Up", "Dn"):
        if sum(1 for x in buys if x["side"] == side) > 3:
            accumulates += 1
            break

print("=== per-window 5m tactic (%d windows) ===" % n)
print("first-BUY minute: median %.1f  mean %.1f  (0=open, 5=close)" %
      (statistics.median(first_min), statistics.mean(first_min)))
print("first-BUY price : median %.2f  mean %.2f  (>0.5 favorite, <0.3 cheap)" %
      (statistics.median(side_first_price), statistics.mean(side_first_price)))
print("two-sided (buys BOTH Up+Dn): %d (%.0f%%)" % (two_sided, 100 * two_sided / n))
print("windows with a SELL        : %d (%.0f%%)" % (sells, 100 * sells / n))
print("accumulates (>3 buys/side) : %d (%.0f%%)" % (accumulates, 100 * accumulates / n))
print("late cheap buy (<0.15, >3.5min): %d (%.0f%%)" % (late_cheap, 100 * late_cheap / n))

print("\nall-BUY price histogram:")
buck = collections.Counter(min(int(p * 10) / 10, 0.9) for p in buy_prices)
for b in sorted(buck):
    print("  %.1f-%.1f  %4d  %s" % (b, b + 0.1, buck[b], "#" * int(40 * buck[b] / len(buy_prices))))

print("\nBUY-minute histogram (when in the window):")
mbuck = collections.Counter(min(int(x["min"]) for x in []) if False else 0 for _ in [0])
mb = collections.Counter(max(0, min(4, int(m))) for m in [x["min"] for w in byw.values() for x in w if x["act"] == "BUY"])
for b in sorted(mb):
    print("  min %d-%d  %4d  %s" % (b, b + 1, mb[b], "#" * int(40 * mb[b] / max(1, sum(mb.values())))))

# sample 3 full windows verbatim
print("\n=== 3 sample windows (verbatim, sorted by minute) ===")
for slug in list(byw)[:3]:
    print(" ", slug)
    for x in sorted(byw[slug], key=lambda x: x["min"]):
        print("    min %+.2f  %s %-4s  %.3f x %.0f" % (x["min"], x["act"], x["side"], x["price"], x["size"]))
