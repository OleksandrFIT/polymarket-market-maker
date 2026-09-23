"""PREDICTIVE regime filter test (Kaufman Efficiency Ratio) on today's wallet data.

ER over trailing N min of BTC, computed AT window open (no look-ahead):
  ER = |close[t] - close[t-N]| / sum(|close[i]-close[i-1]|)
ER near 1 = clean trend (directional ON); near 0 = chop (directional OFF).

For each window we take the "directional bet" = wallet's heavy side, winner = on-chain
(gamma), PnL = winshares - spent. We bucket PnL into ALLOWED (ER>=thr at open) vs
PAUSED, and compare to the ideal (take all + / skip all -). Scans a few N/thr.
Read-only. NOTE: tuning thr on ONE day is in-sample/overfit — needs OOS later.
"""
import urllib.request, json, time, collections, datetime as dt

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
    win = winner(w["slug"])
    if win is None:
        continue
    spent = w["up_usd"] + w["dn_usd"]
    win_sh = w["up_sh"] if win == "Up" else w["dn_sh"]
    rows.append({"ts": w["ts"], "pnl": win_sh - spent})
rows.sort(key=lambda r: r["ts"])
if not rows:
    print("no resolved windows"); raise SystemExit

# Binance 1-min closes covering the day
closes = {}
end = (rows[-1]["ts"] + 1200) * 1000
lo = rows[0]["ts"] - 4000
while True:
    kl = get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000&endTime=%d" % end)
    if not kl:
        break
    for k in kl:
        closes[k[0] // 1000] = float(k[4])
    end = kl[0][0] - 60000
    if kl[0][0] // 1000 <= lo:
        break
    time.sleep(0.05)


def er(ts, n):
    seq = [closes.get(ts - (n - i) * 60) for i in range(n + 1)]
    seq = [x for x in seq if x is not None]
    if len(seq) < 3:
        return None
    net = abs(seq[-1] - seq[0])
    path = sum(abs(seq[i] - seq[i - 1]) for i in range(1, len(seq)))
    return net / path if path > 0 else 0.0


tot = sum(r["pnl"] for r in rows)
ideal_pos = sum(r["pnl"] for r in rows if r["pnl"] > 0)
ideal_neg = sum(r["pnl"] for r in rows if r["pnl"] < 0)
print("resolved windows: %d | day PnL $%+.1f" % (len(rows), tot))
print("ideal filter (take all +, skip all -): allowed $%+.0f / skipped $%+.0f\n" % (ideal_pos, ideal_neg))

print("=== ER filter scan: allowed (we trade) vs paused PnL ===")
print(" N(min) thr   allowed    paused   n_allow")
best = None
for n in (20, 30, 45):
    for thr in (0.25, 0.35, 0.45):
        al = ps = 0.0; na = 0
        for r in rows:
            e = er(r["ts"], n)
            if e is not None and e >= thr:
                al += r["pnl"]; na += 1
            else:
                ps += r["pnl"]
        print("  %2d    %.2f  $%+8.1f  $%+8.1f   %d" % (n, thr, al, ps, na))
        if best is None or al > best[2]:
            best = (n, thr, al, ps, na)
print("\nbest 'allowed' PnL: N=%d thr=%.2f -> allowed $%+.1f / paused $%+.1f (n_allow %d)" % best)

# hourly ER + PnL for the best config to see morning(trend) vs afternoon(chop)
n, thr = best[0], best[1]
print("\n=== by Kyiv hour (N=%d, thr=%.2f) ===" % (n, thr))
print(" hour   avgER  allowed   paused")
byh = collections.defaultdict(lambda: [[], 0.0, 0.0])
for r in rows:
    e = er(r["ts"], n)
    h = (r["ts"] + KYIV) // 3600 % 24
    if e is not None:
        byh[h][0].append(e)
    if e is not None and e >= thr:
        byh[h][1] += r["pnl"]
    else:
        byh[h][2] += r["pnl"]
import statistics
for h in sorted(byh):
    ers, al, ps = byh[h]
    ae = statistics.mean(ers) if ers else 0
    print("  %02d:00  %.2f  $%+8.1f  $%+8.1f" % (h, ae, al, ps))
