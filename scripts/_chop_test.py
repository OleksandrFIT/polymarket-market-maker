"""Does OUR momentum strategy lose in CHOP? Fresh recent 15m windows (incl. today).

Fetches the last N 15m BTC windows ending NOW (so today's chop is included), runs our
momentum signal (detect_bias) per window, and buckets momentum EV/share by the LOCAL
regime measured as trailing-20-window reversal rate (min-10 leader flips by settle).
Also per-UTC-day EV, to see if choppy days go -EV. Read-only.
"""
import sys, os, json, time, statistics, collections, urllib.request, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quoter.config import Config
from quoter.runner.trend_detector import detect_bias, sigma_remaining

UA = {"User-Agent": "Mozilla/5.0"}
PCACHE = "/tmp/poly_path15_fresh"
os.makedirs(PCACHE, exist_ok=True)
STEP = 900
N_WANT = int(sys.argv[1]) if len(sys.argv) > 1 else 700
DEC = 10
FEE = 0.02
NOW = int(time.time())
END_TS = (NOW // STEP) * STEP - STEP   # last fully-closed window


def get(u, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.6)


def up_token(ts):
    r = get("https://gamma-api.polymarket.com/markets?slug=btc-updown-15m-%d&closed=true" % ts)
    if not isinstance(r, list) or not r:
        return None
    toks = r[0].get("clobTokenIds")
    if isinstance(toks, str):
        toks = json.loads(toks)
    return toks[0] if toks else None


def path(ts):
    fn = os.path.join(PCACHE, "%d.json" % ts)
    if os.path.exists(fn):
        d = json.load(open(fn)); return d if d else None
    tok = up_token(ts)
    if not tok:
        json.dump(None, open(fn, "w")); return None
    r = get("https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=1"
            % (tok, ts - 30, ts + STEP + 30))
    h = r.get("history", []) if isinstance(r, dict) else []
    if len(h) < 8:
        json.dump(None, open(fn, "w")); return None
    px = [None] * 15
    for pt in h:
        m = int((pt["t"] - ts) // 60)
        if 0 <= m < 15:
            px[m] = pt["p"]
    last = h[0]["p"]
    for m in range(15):
        if px[m] is None:
            px[m] = last
        last = px[m]
    d = {"up": px, "winner": "Up" if h[-1]["p"] >= 0.5 else "Down", "ts": ts}
    json.dump(d, open(fn, "w")); return d


print("fetching up to %d recent 15m windows ending %s UTC..." %
      (N_WANT, dt.datetime.utcfromtimestamp(END_TS).strftime("%m-%d %H:%M")))
wins = []; ts = END_TS; miss = 0
while len(wins) < N_WANT and miss < 80:
    try:
        d = path(ts)
    except Exception:
        d = None
    if d:
        wins.append(d); miss = 0
    else:
        miss += 1
    ts -= STEP
    time.sleep(0.02)
wins.sort(key=lambda w: w["ts"])
n = len(wins)
print("loaded %d windows over %.1f days (%s .. %s UTC)\n" %
      (n, (wins[-1]["ts"] - wins[0]["ts"]) / 86400,
       dt.datetime.utcfromtimestamp(wins[0]["ts"]).strftime("%m-%d"),
       dt.datetime.utcfromtimestamp(wins[-1]["ts"]).strftime("%m-%d")))

# Binance closes
closes = {}
end = (wins[-1]["ts"] + STEP) * 1000; lo = wins[0]["ts"] - 120
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


def btc(t):
    for d in (0, 60, 120):
        if (t - d) in closes:
            return closes[t - d]
    return None


cfg = Config(trend_confidence=0.35, trend_gate_sec=600.0)
# per-window: momentum EV (if signal), reversal flag
recs = []
for w in wins:
    leader10 = "Up" if w["up"][10] > 0.5 else "Down"
    rev = 1 if leader10 != w["winner"] else 0
    strike = btc(w["ts"]); now = btc(w["ts"] + DEC * 60)
    ev = None
    if strike is not None and now is not None:
        buf = [(btc(w["ts"] + k * 60), float(w["ts"] + k * 60))
               for k in range(max(0, DEC - 8), DEC + 1) if btc(w["ts"] + k * 60) is not None]
        sig = sigma_remaining(buf, 900 - DEC * 60, cfg)
        b = detect_bias(now, strike, sig, 900 - DEC * 60, cfg)
        if b != "NEUTRAL":
            fav = "Up" if b == "UP" else "Down"
            entry = w["up"][DEC] if fav == "Up" else 1 - w["up"][DEC]
            ev = (1 - (entry + FEE)) if w["winner"] == fav else -(entry + FEE)
    recs.append({"ts": w["ts"], "rev": rev, "ev": ev})

evs = [r["ev"] for r in recs if r["ev"] is not None]
print("overall reversal rate: %.0f%% | momentum signals: %d | baseline EV/share %+.4f"
      % (100 * sum(r["rev"] for r in recs) / n, len(evs), statistics.mean(evs)))

# trailing-20 reversal regime -> bucket momentum EV
K = 20
buckets = {"TREND (<20%)": [], "MID (20-35%)": [], "CHOP (>=35%)": []}
revs = []
for r in recs:
    if len(revs) >= K and r["ev"] is not None:
        tr = sum(revs[-K:]) / K
        b = "TREND (<20%)" if tr < 0.20 else ("MID (20-35%)" if tr < 0.35 else "CHOP (>=35%)")
        buckets[b].append(r["ev"])
    revs.append(r["rev"])
print("\n=== momentum EV by trailing-%d-window reversal regime ===" % K)
for b, v in buckets.items():
    if v:
        print("  %-14s n%-4d  EV/share %+.4f  hit %2.0f%%" %
              (b, len(v), statistics.mean(v), 100 * sum(1 for x in v if x > 0) / len(v)))
    else:
        print("  %-14s n0" % b)

# per UTC day: reversal + momentum EV
print("\n=== per UTC day: reversal%% and momentum EV ===")
byday = collections.defaultdict(lambda: {"rev": [], "ev": []})
for r in recs:
    day = dt.datetime.utcfromtimestamp(r["ts"]).strftime("%m-%d")
    byday[day]["rev"].append(r["rev"])
    if r["ev"] is not None:
        byday[day]["ev"].append(r["ev"])
print(" day    n   reversal%  momentumEV  verdict")
for day in sorted(byday):
    d = byday[day]
    rr = 100 * sum(d["rev"]) / len(d["rev"])
    ev = statistics.mean(d["ev"]) if d["ev"] else 0
    verd = "CHOP" if rr >= 35 else ("ok" if rr >= 25 else "trend")
    print("  %s  %3d   %4.0f%%    %+.4f   %s%s" %
          (day, len(d["rev"]), rr, ev, verd, "  <- EV NEGATIVE" if ev < 0 else ""))
print("\nKEY: if CHOP-regime / high-reversal days show NEGATIVE momentum EV -> we DO need")
print("a regime filter, and reversal-rate (not ER) is the signal to build it on.")

# ---- decay trajectory: weekly EV + first vs second half ----
print("\n=== EV trajectory (is the edge decaying?) ===")
seq = [r for r in recs if r["ev"] is not None]
# weekly buckets by ISO-ish week (group by 7-day blocks from the start)
t0 = seq[0]["ts"]
wk = collections.defaultdict(list)
for r in seq:
    wk[(r["ts"] - t0) // (7 * 86400)].append(r["ev"])
for k in sorted(wk):
    d0 = dt.datetime.utcfromtimestamp(t0 + k * 7 * 86400).strftime("%m-%d")
    print("  week %d (from %s): n%-4d  EV/share %+.4f" % (k + 1, d0, len(wk[k]), statistics.mean(wk[k])))
half = len(seq) // 2
fh = [r["ev"] for r in seq[:half]]; sh = [r["ev"] for r in seq[half:]]
print("  first half  EV %+.4f (n%d)" % (statistics.mean(fh), len(fh)))
print("  second half EV %+.4f (n%d)" % (statistics.mean(sh), len(sh)))
print("  -> if second half << first half (toward <=0), the edge is decaying out-of-sample.")
