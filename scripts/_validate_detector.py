"""Validate the LIVE trend_detector (win_prob model) vs the proven move-threshold
rule, on the pooled ~3000 cached 15m windows.

Imports the REAL functions (detect_bias, sigma_remaining) from the bot so we test
the exact code that would run live. For each window at decision minute m we feed
the detector BTC(open)=strike, BTC(now), a recent price buffer for sigma, and
time_left, get its UP/DOWN/NEUTRAL bias, then score favorite EV/share (causal,
hold to resolution). Compared head-to-head with |move|>thr at the same minute.
Read-only.
"""
import sys, os, json, time, statistics, collections, datetime as dt
import urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quoter.config import Config
from quoter.runner.trend_detector import detect_bias, sigma_remaining

UA = {"User-Agent": "Mozilla/5.0"}
CACHE_DIRS = ["/tmp/poly_path15_cache", "/tmp/poly_path15_oos"]
STEP = 900
SLIP = 0.02


def get(url, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.6)


# load windows
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
print("pooled %d windows" % len(wins))

# clusters + binance closes
clusters, cur = [], [wins[0]]
for w in wins[1:]:
    if w["ts"] - cur[-1]["ts"] > 7200:
        clusters.append(cur); cur = [w]
    else:
        cur.append(w)
clusters.append(cur)
closes = {}
for c in clusters:
    end = (c[-1]["ts"] + STEP) * 1000
    lo = c[0]["ts"] - 720
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
print("binance closes: %d min\n" % len(closes))


def btc_at(t):
    for d in (0, 60, 120):
        if (t - d) in closes:
            return closes[t - d]
    return None


def ev_of(side, w, minute, slip):
    price = w["up"][minute] if side == "Up" else 1 - w["up"][minute]
    entry = price + slip
    return (1 - entry) if w["winner"] == side else -entry


def detector_bias(w, minute, cfg):
    strike = btc_at(w["ts"])
    now = btc_at(w["ts"] + minute * 60)
    if strike is None or now is None:
        return "NEUTRAL"
    buf = []
    for k in range(max(0, minute - 8), minute + 1):
        p = btc_at(w["ts"] + k * 60)
        if p is not None:
            buf.append((p, float(w["ts"] + k * 60)))
    time_left = 900 - minute * 60
    sig = sigma_remaining(buf, time_left, cfg)
    return detect_bias(now, strike, sig, time_left, cfg)


def move_bias(w, minute, thr):
    a, b = btc_at(w["ts"]), btc_at(w["ts"] + minute * 60)
    if a is None or b is None:
        return "NEUTRAL"
    if abs(b - a) <= thr:
        return "NEUTRAL"
    return "UP" if b > a else "DOWN"


print("="*70)
print("HEAD-TO-HEAD: live trend_detector vs move-threshold  (EV/share @ slip=2c)")
print("="*70)
print("%-34s  n_fire  fire%%  hit%%   EV/share" % "strategy")

for minute in (10, 12):
    # move rule baselines
    for thr in (20, 30):
        evs = []
        for w in wins:
            bias = move_bias(w, minute, thr)
            if bias == "NEUTRAL":
                continue
            evs.append(ev_of("Up" if bias == "UP" else "Down", w, minute, SLIP))
        fr = 100*len(evs)/len(wins)
        hit = 100*sum(1 for e in evs if e > 0)/len(evs) if evs else 0
        print("  move min%d |move|>$%-3d            %5d  %4.0f%%  %3.0f%%  %+.3f"
              % (minute, thr, len(evs), fr, hit, statistics.mean(evs) if evs else 0))
    # detector at several confidence knobs
    for conf in (0.40, 0.35, 0.30):
        cfg = Config(trend_confidence=conf, trend_gate_sec=600.0)
        evs = []
        for w in wins:
            bias = detector_bias(w, minute, cfg)
            if bias == "NEUTRAL":
                continue
            evs.append(ev_of("Up" if bias == "UP" else "Down", w, minute, SLIP))
        fr = 100*len(evs)/len(wins)
        hit = 100*sum(1 for e in evs if e > 0)/len(evs) if evs else 0
        print("  detector min%d conf=%.2f           %5d  %4.0f%%  %3.0f%%  %+.3f"
              % (minute, conf, len(evs), fr, hit, statistics.mean(evs) if evs else 0))
    print()

# regime split for the detector (conf 0.40, min10)
print("="*70)
print("DETECTOR regime split (min10, conf0.40, slip2c) — trailing 20-win reversal")
print("="*70)
cfg = Config(trend_confidence=0.40, trend_gate_sec=600.0)
buckets = collections.defaultdict(list)
for c in clusters:
    revs = []
    for w in c:
        if len(revs) >= 20:
            tr = sum(revs[-20:]) / 20
            bias = detector_bias(w, 10, cfg)
            if bias != "NEUTRAL":
                ev = ev_of("Up" if bias == "UP" else "Down", w, 10, SLIP)
                b = "TREND(<20%)" if tr < 0.20 else ("MID(20-35%)" if tr < 0.35 else "CHOP(>35%)")
                buckets[b].append(ev)
        revs.append(1 if ("Up" if w["up"][10] > 0.5 else "Down") != w["winner"] else 0)
for b in ("TREND(<20%)", "MID(20-35%)", "CHOP(>35%)"):
    v = buckets.get(b, [])
    if v:
        print("  %-12s n%-4d EV%+.3f hit%2.0f%%" % (b, len(v), statistics.mean(v),
                                                    100*sum(1 for x in v if x > 0)/len(v)))
    else:
        print("  %-12s n0" % b)
print("\nVERDICT: use detector if its EV >= move-rule EV at comparable fire%.")
