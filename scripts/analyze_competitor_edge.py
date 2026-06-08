"""Verify the latency-arbitrage diagnosis on Bonereaper's real trades.

For each trade: parse the window (slug -> asset, tf, open_ts), the side he bought,
the price, and the time-into-window. Determine the window's WINNER from Binance
1m klines (close-of-window vs open-of-window). Then compute HIS realized entry
win-rate by price bucket and by timing — the decisive test of whether he buys
cheap-AND-right (latency-arb signature) vs cheap-and-wrong (adverse selection,
like us).
"""
import json
import re
import urllib.request
from collections import defaultdict

ADDR = "0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
SLUG_RE = re.compile(r"(btc|eth)-updown-(5m|15m)-(\d+)")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


# 1. Pull trades (paginate)
trades = []
for off in range(0, 2000, 500):
    batch = get(
        f"https://data-api.polymarket.com/activity?user={ADDR}"
        f"&limit=500&offset={off}&sortBy=TIMESTAMP&sortDirection=DESC"
    )
    if not batch:
        break
    trades += [t for t in batch if t.get("type") == "TRADE"]
print(f"trades fetched: {len(trades)}")

rows = []
for t in trades:
    m = SLUG_RE.search(t.get("slug", ""))
    if not m:
        continue
    asset, tf, open_ts = m.group(1).upper(), m.group(2), int(m.group(3))
    dur = 300 if tf == "5m" else 900
    rows.append({
        "asset": asset, "tf": tf, "open_ts": open_ts, "expire_ts": open_ts + dur,
        "outcome": t["outcome"],          # 'Up' | 'Down'
        "price": float(t["price"]),
        "size": float(t["size"]),
        "secs_in": int(t["timestamp"]) - open_ts,
    })
print(f"parsed updown trades: {len(rows)}")
if not rows:
    raise SystemExit("no updown trades parsed")

# 2. Binance klines -> winner per window
def klines(symbol, start_ms, end_ms):
    out = {}
    cur = start_ms
    while cur < end_ms:
        data = get(
            f"https://api.binance.com/api/v3/klines?symbol={symbol}"
            f"&interval=1m&startTime={cur}&endTime={end_ms}&limit=1000"
        )
        if not data:
            break
        for k in data:
            out[k[0] // 1000] = float(k[1])  # open time (s) -> open price
        cur = data[-1][0] + 60_000
        if len(data) < 1000:
            break
    return out

price_at = {}
for asset, sym in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
    ts = [r for r in rows if r["asset"] == asset]
    if not ts:
        continue
    lo = min(r["open_ts"] for r in ts) * 1000
    hi = (max(r["expire_ts"] for r in ts) + 120) * 1000
    price_at[asset] = klines(sym, lo, hi)
    print(f"{asset}: {len(price_at[asset])} 1m candles")

def px(asset, sec):
    d = price_at.get(asset, {})
    minute = (sec // 60) * 60
    for off in range(0, 600, 60):  # walk forward to nearest available candle
        if minute + off in d:
            return d[minute + off]
    return None

graded = []
for r in rows:
    o = px(r["asset"], r["open_ts"])
    c = px(r["asset"], r["expire_ts"])
    if o is None or c is None or o == c:
        continue
    winner = "Up" if c > o else "Down"
    r["won"] = (r["outcome"] == winner)
    graded.append(r)
print(f"graded (winner resolvable): {len(graded)}\n")

# 3. His win-rate by PRICE bucket (the decisive comparison vs our table)
def bucket(p):
    if p < 0.45: return "1) <0.45"
    if p < 0.55: return "2) 0.45-0.55"
    if p < 0.65: return "3) 0.55-0.65"
    if p < 0.80: return "4) 0.65-0.80"
    return "5) >=0.80"

agg = defaultdict(lambda: [0, 0, 0.0, 0.0])  # n, won, sum_price, sum_size
for r in graded:
    b = agg[bucket(r["price"])]
    b[0] += 1; b[1] += int(r["won"]); b[2] += r["price"]; b[3] += r["size"]

print("=== BONEREAPER: entry win-rate by price bucket ===")
print(f"{'bucket':<14}{'trades':>7}{'avg_px':>8}{'win%':>7}{'$vol':>9}")
for k in sorted(agg):
    n, won, sp, sz = agg[k]
    print(f"{k:<14}{n:>7}{sp/n:>8.3f}{100*won/n:>6.1f}%{sz:>9.0f}")

tot_n = len(graded)
tot_won = sum(r["won"] for r in graded)
tot_szw = sum(r["size"] for r in graded)
tot_szwon = sum(r["size"] for r in graded if r["won"])
avg_px = sum(r["price"] for r in graded) / tot_n
print(f"\nOVERALL: {tot_n} trades, avg_px {avg_px:.3f}, "
      f"trade-win {100*tot_won/tot_n:.1f}%, share-wtd-win {100*tot_szwon/tot_szw:.1f}%")

# 4. Timing-into-window: when does he buy, and does early differ from late?
def tbucket(s):
    if s < 30: return "0-30s (open)"
    if s < 90: return "30-90s"
    if s < 180: return "90-180s"
    return "180s+ (late)"

tg = defaultdict(lambda: [0, 0, 0.0])
for r in graded:
    if r["tf"] != "5m":
        continue
    b = tg[tbucket(r["secs_in"])]
    b[0] += 1; b[1] += int(r["won"]); b[2] += r["price"]
print("\n=== BONEREAPER 5m: timing within window ===")
print(f"{'when':<16}{'trades':>7}{'avg_px':>8}{'win%':>7}")
for k in ["0-30s (open)", "30-90s", "90-180s", "180s+ (late)"]:
    if k in tg:
        n, won, sp = tg[k]
        print(f"{k:<16}{n:>7}{sp/n:>8.3f}{100*won/n:>6.1f}%")
