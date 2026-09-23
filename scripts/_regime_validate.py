"""Validate a PREVENTIVE regime filter over the 4-month pool (3000 windows) against
OUR momentum-tilt EV (not the competitor's HF laddering, not one day).

For each 15m window:
  - regime signal computed at WINDOW OPEN from trailing BTC (no look-ahead):
      ER  = Kaufman efficiency ratio over trailing N min
      VOL = stdev of 1-min returns over trailing N min (annualization-free)
  - our momentum bet: at decision min, favorite = detect_bias side; entry = mid;
      ev = (1-(entry+fee)) if favorite wins else -(entry+fee)   [fee=0.02]
Bucket momentum-EV by regime signal. If EV rises with the signal, the filter works
and we can set a threshold FROM MULTI-MONTH DATA. Read-only.
"""
import sys, os, json, time, statistics, urllib.request, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quoter.config import Config
from quoter.runner.trend_detector import detect_bias, sigma_remaining

UA = {"User-Agent": "Mozilla/5.0"}
DIRS = ["/tmp/poly_path15_cache", "/tmp/poly_path15_oos"]
STEP = 900
DEC = 10
FEE = 0.02


def get(u, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.5)


byts = {}
for d in DIRS:
    if not os.path.isdir(d):
        continue
    for fn in os.listdir(d):
        try:
            w = json.load(open(os.path.join(d, fn)))
        except Exception:
            w = None
        if w and w.get("up") and w.get("winner") and w.get("ts"):
            byts[w["ts"]] = w
wins = sorted(byts.values(), key=lambda w: w["ts"])
print("pooled %d windows" % len(wins))
clusters, cur = [], [wins[0]]
for w in wins[1:]:
    if w["ts"] - cur[-1]["ts"] > 7200:
        clusters.append(cur); cur = [w]
    else:
        cur.append(w)
clusters.append(cur)
closes = {}
for c in clusters:
    end = (c[-1]["ts"] + STEP) * 1000; lo = c[0]["ts"] - 3600
    while True:
        kl = get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000&endTime=%d" % end)
        if not kl:
            break
        for k in kl:
            closes[k[0] // 1000] = float(k[4])
        end = kl[0][0] - 60000
        if kl[0][0] // 1000 <= lo:
            break
        time.sleep(0.04)
print("binance closes: %d\n" % len(closes))


def seq(ts, n):
    s = [closes.get(ts - (n - i) * 60) for i in range(n + 1)]
    return [x for x in s if x is not None]


def er(ts, n):
    s = seq(ts, n)
    if len(s) < 5:
        return None
    net = abs(s[-1] - s[0]); path = sum(abs(s[i] - s[i - 1]) for i in range(1, len(s)))
    return net / path if path > 0 else 0.0


def vol(ts, n):
    s = seq(ts, n)
    if len(s) < 5:
        return None
    rets = [s[i] - s[i - 1] for i in range(1, len(s))]
    return statistics.pstdev(rets)


cfg = Config(trend_confidence=0.35, trend_gate_sec=600.0)
data = []   # (er, vol, ev, won)
for w in wins:
    strike = closes.get(w["ts"]); now = closes.get(w["ts"] + DEC * 60)
    if strike is None or now is None:
        continue
    buf = [(closes.get(w["ts"] + k * 60), float(w["ts"] + k * 60))
           for k in range(max(0, DEC - 8), DEC + 1) if closes.get(w["ts"] + k * 60) is not None]
    sig = sigma_remaining(buf, 900 - DEC * 60, cfg)
    bias = detect_bias(now, strike, sig, 900 - DEC * 60, cfg)
    if bias == "NEUTRAL":
        continue
    fav = "Up" if bias == "UP" else "Down"
    entry = w["up"][DEC] if fav == "Up" else 1 - w["up"][DEC]
    won = (w["winner"] == fav)
    ev = (1 - (entry + FEE)) if won else -(entry + FEE)
    e = er(w["ts"], 30); v = vol(w["ts"], 30)
    if e is None or v is None:
        continue
    data.append((e, v, ev, won))

n = len(data)
base_ev = statistics.mean(d[2] for d in data)
print("momentum signals with regime data: %d | baseline EV/share %+.4f (no filter)\n" % (n, base_ev))

# bucket EV by ER
print("=== momentum EV by trailing-30m ER (efficiency ratio) ===")
print(" ER bucket    n     EV/share   hit%   (cum EV if we REQUIRE ER>=lo)")
ds = sorted(data, key=lambda d: d[0])
edges = [0, 0.15, 0.25, 0.35, 0.5, 1.01]
for i in range(len(edges) - 1):
    lo, hi = edges[i], edges[i + 1]
    b = [d for d in data if lo <= d[0] < hi]
    if not b:
        continue
    ev = statistics.mean(d[2] for d in b); hit = sum(1 for d in b if d[3]) / len(b)
    keep = [d for d in data if d[0] >= lo]
    cev = statistics.mean(d[2] for d in keep) if keep else 0
    print("  %.2f-%.2f  %4d   %+.4f   %3.0f%%   require>= %.2f -> EV %+.4f (n%d)" %
          (lo, hi, len(b), ev, 100 * hit, lo, cev, len(keep)))

# bucket EV by VOL
print("\n=== momentum EV by trailing-30m BTC vol (stdev of 1m moves, $) ===")
vs = sorted(d[1] for d in data)
qs = [vs[int(len(vs) * f)] for f in (0, 0.2, 0.4, 0.6, 0.8)] + [vs[-1] + 1]
print(" vol bucket($)      n     EV/share   hit%")
for i in range(len(qs) - 1):
    lo, hi = qs[i], qs[i + 1]
    b = [d for d in data if lo <= d[1] < hi]
    if not b:
        continue
    ev = statistics.mean(d[2] for d in b); hit = sum(1 for d in b if d[3]) / len(b)
    print("  %6.1f-%6.1f   %4d   %+.4f   %3.0f%%" % (lo, hi, len(b), ev, 100 * hit))

print("\nIf EV climbs monotonically with ER/vol, a PREVENTIVE filter works:")
print("trade momentum only when the trailing regime signal is above the break-even level.")
