"""STEP 2 — slippage-aware + regime-filtered momentum backtest.

Pools ALL cached 15m windows (in-sample + OOS, ~Mar-Jun) and stress-tests the
momentum/favorite tilt under two real-world frictions:

  (A) SLIPPAGE: a taker buying the favorite pays the ASK, not the mid. We haircut
      every entry by `slip` cents. EV_net/share = EV_0 - slip. Survives if > 0.

  (B) REGIME: the edge was only ever measured in TREND regimes. We compute, with
      NO look-ahead, a trailing reversal-rate over the previous K windows and bucket
      each signal by it (trend / mid / chop). Shows whether a regime gate is needed
      and what it would have protected.

Signal (causal): at minute m, if |BTC move since open| > thr, buy the favorite
(side of the move) at its mid + slip; hold to resolution. Read-only.
"""
import urllib.request, json, os, time, statistics, collections, datetime as dt

UA = {"User-Agent": "Mozilla/5.0"}
CACHE_DIRS = ["/tmp/poly_path15_cache", "/tmp/poly_path15_oos"]
STEP = 900


def get(url, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.6)


# ---- load + dedup all cached windows ----
byts = {}
for d in CACHE_DIRS:
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
n = len(wins)
print("pooled %d unique 15m windows" % n)

# ---- clusters (gap > 2h) + Binance closes per cluster ----
clusters, cur = [], [wins[0]]
for w in wins[1:]:
    if w["ts"] - cur[-1]["ts"] > 7200:
        clusters.append(cur); cur = [w]
    else:
        cur.append(w)
clusters.append(cur)
print("clusters: %d  spans: %s" % (len(clusters),
      ", ".join(dt.datetime.utcfromtimestamp(c[0]["ts"]).strftime("%m-%d") +
                "->" + dt.datetime.utcfromtimestamp(c[-1]["ts"]).strftime("%m-%d") for c in clusters)))

closes = {}
for c in clusters:
    end = (c[-1]["ts"] + STEP) * 1000
    lo = c[0]["ts"] - 120
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
print("binance closes loaded: %d minutes\n" % len(closes))


def btc_at(t):
    for d in (0, 60, 120):
        if (t - d) in closes:
            return closes[t - d]
    return None


# ---- per-window: reversal flag + signal cell ----
def signal_ev(w, minute, thr, slip):
    """returns (entry_price, won) for the favorite if signal fires, else None."""
    a, b = btc_at(w["ts"]), btc_at(w["ts"] + minute*60)
    if a is None or b is None:
        return None
    mov = b - a
    if abs(mov) <= thr:
        return None
    side = "Up" if mov > 0 else "Down"
    price = w["up"][minute] if side == "Up" else 1 - w["up"][minute]
    return (price + slip, w["winner"] == side)


# ============ (A) SLIPPAGE SENSITIVITY ============
print("="*68)
print("(A) SLIPPAGE SENSITIVITY  (EV/share, pooled %d windows)" % n)
print("="*68)
print("rule           slip=0c   slip=1c   slip=2c   slip=3c   (n fired, hit%)")
for minute, thr in [(10, 20), (10, 40), (12, 30)]:
    line = "min%d>$%-3d  " % (minute, thr)
    nf = hit = 0
    evs = {}
    for slip in (0.0, 0.01, 0.02, 0.03):
        vals = []
        for w in wins:
            r = signal_ev(w, minute, thr, slip)
            if r:
                entry, won = r
                vals.append((1 - entry) if won else -entry)
        evs[slip] = statistics.mean(vals) if vals else float("nan")
        if slip == 0.0:
            nf = len(vals); hit = sum(1 for w in wins
                                      for r in [signal_ev(w, minute, thr, 0.0)]
                                      if r and r[1]) / nf
    print("%s  %+.3f    %+.3f    %+.3f    %+.3f   (n%d, %2.0f%%)" %
          (line, evs[0.0], evs[0.01], evs[0.02], evs[0.03], nf, 100*hit))

# ============ (B) REGIME CONDITIONING ============
# trailing reversal-rate over previous K windows (causal, within cluster)
K = 20
print("\n" + "="*68)
print("(B) REGIME GATE  — EV/share @ slip=2c, by trailing %d-window reversal-rate" % K)
print("="*68)
SLIP = 0.02
minute, thr = 10, 30
buckets = {"TREND (<20%)": [], "MID (20-35%)": [], "CHOP (>35%)": []}
fire_by_bucket = collections.Counter()
for c in clusters:
    revs = []
    for w in c:
        leader10 = "Up" if w["up"][10] > 0.5 else "Down"
        revflag = 1 if leader10 != w["winner"] else 0
        if len(revs) >= K:
            tr = sum(revs[-K:]) / K
            r = signal_ev(w, minute, thr, SLIP)
            if r:
                entry, won = r
                ev = (1 - entry) if won else -entry
                b = "TREND (<20%)" if tr < 0.20 else ("MID (20-35%)" if tr < 0.35 else "CHOP (>35%)")
                buckets[b].append(ev); fire_by_bucket[b] += 1
        revs.append(revflag)
for b, vals in buckets.items():
    if vals:
        print("  %-15s n%-4d  EV%+.3f/share  hit%2.0f%%" %
              (b, len(vals), statistics.mean(vals), 100*sum(1 for v in vals if v > 0)/len(vals)))
    else:
        print("  %-15s n0    (regime never occurred in sample)" % b)

# ============ (C) $ PROJECTION ============
print("\n" + "="*68)
print("(C) DAILY $ PROJECTION  (min10>$30, slip=2c, regime-gated to non-CHOP)")
print("="*68)
gated = buckets["TREND (<20%)"] + buckets["MID (20-35%)"]
if gated:
    ev = statistics.mean(gated)
    fired = len(gated)
    span_days = (wins[-1]["ts"] - wins[0]["ts"]) / 86400
    # crude: windows actually present span fraction; use fires/day from sample density
    days_covered = sum((c[-1]["ts"] - c[0]["ts"]) / 86400 for c in clusters)
    fires_per_day = fired / days_covered if days_covered else 0
    for size in (5, 25, 100):
        print("  size %3d sh: EV/share %+.3f x %d sh x %.1f fires/day = $%+.1f/day"
              % (size, ev, size, fires_per_day, ev * size * fires_per_day))
    print("  (gated EV/share %+.3f, %d fires over %.1f covered days = %.1f/day)"
          % (ev, fired, days_covered, fires_per_day))
print("\nSurvives if (A) EV>0 at slip=2c AND (B) non-CHOP buckets stay +EV.")
