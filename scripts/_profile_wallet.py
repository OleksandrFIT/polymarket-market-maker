"""Characterize a wallet's TACTIC from its trades (not volume).
Usage: python3 scripts/_profile_wallet.py <address> [hours]
"""
import urllib.request, json, time, sys, collections, statistics, datetime as dt

ADDR = sys.argv[1] if len(sys.argv) > 1 else "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
HOURS = int(sys.argv[2]) if len(sys.argv) > 2 else 48
BASE = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0"}
NOW = int(time.time()); SINCE = NOW - HOURS * 3600


def get(u, tries=4):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return {"ERR": "fail"}
            time.sleep(0.8)


trades, off = [], 0
while True:
    b = get("%s/trades?user=%s&limit=500&offset=%d" % (BASE, ADDR, off))
    if not isinstance(b, list):      # transient fail after retries — stop, keep what we have
        break
    if not b:
        break
    keep = [t for t in b if t.get("timestamp", 0) >= SINCE]
    trades += keep
    if len(keep) < len(b) or len(b) < 500:   # reached the time cutoff or last page
        break
    off += 500; time.sleep(0.15)

print("wallet %s" % ADDR)
print("trades last %dh: %d" % (HOURS, len(trades)))
if not trades:
    print("no trades (check address / endpoint)"); sys.exit()

# sides, sizes, prices
sides = collections.Counter(t.get("side") for t in trades)
print("sides:", dict(sides))
sizes = [float(t.get("size", 0)) for t in trades]
prices = [float(t.get("price", 0)) for t in trades]
usd = [float(t.get("size", 0)) * float(t.get("price", 0)) for t in trades]
print("trade size: median %.0f  mean %.0f  max %.0f shares" % (statistics.median(sizes), statistics.mean(sizes), max(sizes)))
print("trade $: median $%.1f  mean $%.1f  total $%.0f" % (statistics.median(usd), statistics.mean(usd), sum(usd)))

# market types: asset / timeframe from slug & title
def cat(t):
    s = t.get("slug", "") or ""
    ti = (t.get("title", "") or "").lower()
    tf = "5m" if "-5m-" in s else ("15m" if "-15m-" in s else ("1h" if "-1h-" in s or "hourly" in ti else ("1d" if "-1d-" in s else "?")))
    if "updown" in s or "up or down" in ti:
        asset = s.split("-")[0].upper() if s else "?"
        return "%s updown %s" % (asset, tf)
    # non-crypto-updown: bucket by leading words
    return (t.get("title", "")[:40] or "?")

cats = collections.Counter(cat(t) for t in trades)
print("\ntop market types:")
for c, n in cats.most_common(12):
    print("  %5d  %s" % (n, c))

# price histogram (tactic: longshots? favorites? mid?)
print("\nentry-price distribution:")
buck = collections.Counter(int(min(p, 0.999) * 10) / 10 for p in prices)
for b in sorted(buck):
    bar = "#" * int(40 * buck[b] / len(prices))
    print("  %.1f-%.1f  %5d  %s" % (b, b + 0.1, buck[b], bar))

# per-market: both-sided or one-sided? hold or sell?
bym = collections.defaultdict(lambda: {"buy_up": 0, "buy_dn": 0, "sell": 0, "other": 0})
for t in trades:
    m = bym[t.get("conditionId")]
    side = t.get("side"); oi = t.get("outcomeIndex")
    if side == "SELL":
        m["sell"] += 1
    elif oi == 0:
        m["buy_up"] += 1
    elif oi == 1:
        m["buy_dn"] += 1
    else:
        m["other"] += 1
nm = len(bym)
two_sided = sum(1 for m in bym.values() if m["buy_up"] > 0 and m["buy_dn"] > 0)
one_sided = sum(1 for m in bym.values() if (m["buy_up"] > 0) != (m["buy_dn"] > 0))
has_sell = sum(1 for m in bym.values() if m["sell"] > 0)
print("\nmarkets touched: %d" % nm)
print("  two-sided (buys BOTH outcomes): %d (%.0f%%)" % (two_sided, 100 * two_sided / nm))
print("  one-sided (single outcome)    : %d (%.0f%%)" % (one_sided, 100 * one_sided / nm))
print("  markets with any SELL          : %d (%.0f%%)" % (has_sell, 100 * has_sell / nm))
print("\nname/pseudonym:", trades[0].get("name"), "/", trades[0].get("pseudonym"))

# ---- directional edge: does the bought side win more than its entry price? ----
# aggregate per market: net shares per outcome, $ spent; resolve winner via gamma.
def winner(slug):
    r = get("https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % slug)
    if not isinstance(r, list) or not r:
        return None
    op = r[0].get("outcomePrices")
    if isinstance(op, str):
        try: op = json.loads(op)
        except Exception: return None
    if not op or len(op) < 2: return None
    if float(op[0]) >= 0.99: return 0
    if float(op[1]) >= 0.99: return 1
    return None

mk = collections.defaultdict(lambda: {"slug": "", "sh": [0.0, 0.0], "usd": [0.0, 0.0]})
for t in trades:
    if t.get("side") != "BUY": continue
    m = mk[t["conditionId"]]; m["slug"] = t.get("slug", "")
    oi = t.get("outcomeIndex")
    if oi in (0, 1):
        m["sh"][oi] += float(t["size"]); m["usd"][oi] += float(t["size"]) * float(t["price"])

import random
items = list(mk.values()); random.seed(1)
if len(items) > 200: items = random.sample(items, 200)   # cap resolution calls
wins = entries = spent = payout = 0; n = 0; pnl = 0.0
for m in items:
    w = winner(m["slug"])
    if w is None: continue
    heavy = 0 if m["sh"][0] >= m["sh"][1] else 1     # the side they bet (one-sided mostly)
    sh = m["sh"][heavy]; cost = m["usd"][heavy]
    if sh <= 0: continue
    n += 1; entries += cost / sh; wins += 1 if w == heavy else 0
    spent += (m["usd"][0] + m["usd"][1])
    payout += (m["sh"][w] if w in (0, 1) else 0)
    pnl += (m["sh"][w] - (m["usd"][0] + m["usd"][1]))
if n:
    print("\n=== directional edge (resolved markets: %d) ===" % n)
    print("  win-rate of the side they bet : %.0f%%" % (100 * wins / n))
    print("  avg entry price of that side  : %.3f" % (entries / n))
    print("  edge (winrate - entry)        : %+.3f  %s" %
          (wins / n - entries / n, "(+ => real directional alpha)" if wins / n - entries / n > 0.02 else "(~efficient / no clear edge)"))
    print("  realized PnL (hold to resolve): $%+.1f on $%.0f spent (%.1f%%)" %
          (pnl, spent, 100 * pnl / spent if spent else 0))
