# scripts/_replay_tilt.py
"""Deterministic replay of the REAL tilt planner + regime tracker over cached 15m
windows. Measures avg favorite entry, CB false-pause rate on trend data, and the
gated paper-EV. Read-only; imports the live modules so we test code, not formulas."""
import sys, os, json, time, statistics, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quoter.config import Config
from quoter.runner.trend_detector import detect_bias, sigma_remaining
from quoter.runner.regime_tracker import RegimeTracker
from quoter.runner.tilt_planner import plan_tilt

UA = {"User-Agent": "Mozilla/5.0"}
DIRS = ["/tmp/poly_path15_cache", "/tmp/poly_path15_oos"]
STEP = 900
DECISION_MIN = 10


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
    end = (c[-1]["ts"] + STEP) * 1000; lo = c[0]["ts"] - 720
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


def btc(t):
    for d in (0, 60, 120):
        if (t - d) in closes:
            return closes[t - d]
    return None


cfg = Config(trend_confidence=0.35, trend_gate_sec=600.0)
rt = RegimeTracker(cfg.regime_window, cfg.regime_min_samples, cfg.regime_min_ev, cfg.tilt_fee)
entries, gated_ev, paused, fired, false_pause = [], [], 0, 0, 0
for w in wins:
    strike = btc(w["ts"]); now = btc(w["ts"] + DECISION_MIN * 60)
    if strike is None or now is None:
        continue
    buf = [(btc(w["ts"] + k * 60), float(w["ts"] + k * 60))
           for k in range(max(0, DECISION_MIN - 8), DECISION_MIN + 1) if btc(w["ts"] + k * 60) is not None]
    sig = sigma_remaining(buf, 900 - DECISION_MIN * 60, cfg)
    bias = detect_bias(now, strike, sig, 900 - DECISION_MIN * 60, cfg)
    if bias == "NEUTRAL":
        continue
    fav = "Up" if bias == "UP" else "Down"
    entry = w["up"][DECISION_MIN] if fav == "Up" else 1 - w["up"][DECISION_MIN]
    entries.append(entry)
    enabled = rt.directional_enabled()
    win = w["winner"]
    realized = (1 - (entry + cfg.tilt_fee)) if fav == win else -(entry + cfg.tilt_fee)
    if enabled:
        fired += 1; gated_ev.append(realized)
    else:
        paused += 1
        if realized > 0:
            false_pause += 1
    rt.record(fav, entry, win)

print("\nsignals: %d  | tilt FIRED: %d  | tilt PAUSED: %d" % (len(entries), fired, paused))
print("avg_fav_entry: %.3f (median %.3f)" % (statistics.mean(entries), statistics.median(entries)))
print("breakeven hit (entry+fee): %.3f" % (statistics.mean(entries) + cfg.tilt_fee))
if gated_ev:
    print("gated paper-EV/share: %+.4f  (n=%d)" % (statistics.mean(gated_ev), len(gated_ev)))
print("false-pause rate (paused but +EV): %.0f%% of paused" % (100 * false_pause / paused if paused else 0))
print("\nCALIBRATE: set regime_min_ev so gated-EV stays >0; tilt_max_price near avg_fav_entry+margin.")
