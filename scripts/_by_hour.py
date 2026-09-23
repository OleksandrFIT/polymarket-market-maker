"""TIME-OF-DAY analysis on REAL 15m windows — which UTC hours is the strategy
+ (trending, favorite holds) vs - (choppy, reversals catch the naked loser)?

The competitor trades ONLY ~15-17 UTC; our live losses came at 20-21 UTC. This
tests, per UTC hour:
  - reversal rate: % of windows where the minute-10 leader FLIPS by resolution
    (high reversal = choppy = where our naked catches the loser = bad hour)
  - momentum-tilt EV: buy the favorite when BTC moved >$20 at min10 (the +EV bet)
  - avg |BTC move| (trend strength) and window count (liquidity proxy)

Reuses the 15m path cache from _paper_tilt + Binance closes. Read-only, no live.
"""
import urllib.request, json, os, time, statistics, collections

UA = {"User-Agent": "Mozilla/5.0"}
PCACHE = "/tmp/poly_path15_cache"
os.makedirs(PCACHE, exist_ok=True)
STEP = 900
END_TS = 1781631000
N_WANT = 900
THR = 20


def _get(url, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.6)


def up_token(ts):
    r = _get("https://gamma-api.polymarket.com/markets?slug=btc-updown-15m-%d&closed=true" % ts)
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
    r = _get("https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=1"
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


print("loading up to %d 15m windows..." % N_WANT)
wins = []
ts = END_TS; miss = 0
while len(wins) < N_WANT and miss < 100:
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
print("loaded %d windows over %.1f days\n" % (n, (wins[-1]["ts"]-wins[0]["ts"])/86400 if n else 0))

# Binance 1m closes for momentum
closes = {}
end = (wins[-1]["ts"] + STEP) * 1000
lo = wins[0]["ts"] - 120
while True:
    kl = _get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000&endTime=%d" % end)
    if not kl:
        break
    for k in kl:
        closes[k[0] // 1000] = float(k[4])
    end = kl[0][0] - 60000
    if kl[0][0] // 1000 <= lo:
        break


def btc_at(t):
    for d in (0, 60):
        if (t - d) in closes:
            return closes[t - d]
    return None


H = collections.defaultdict(lambda: {"n": 0, "rev": 0, "tilt": [], "mom": []})
for w in wins:
    hour = (w["ts"] // 3600) % 24
    leader10 = "Up" if w["up"][10] > 0.5 else "Down"
    rev = (leader10 != w["winner"])
    b_now = btc_at(w["ts"] + 600); b_open = btc_at(w["ts"])
    rec = H[hour]
    rec["n"] += 1
    rec["rev"] += 1 if rev else 0
    if b_now is not None and b_open is not None:
        mom = b_now - b_open
        rec["mom"].append(abs(mom))
        if abs(mom) > THR:
            side = "Up" if mom > 0 else "Down"
            price = w["up"][10] if side == "Up" else 1 - w["up"][10]
            won = (side == w["winner"])
            rec["tilt"].append(5*(1-price) if won else -5*price)

print("UTC  n   reversal%  avg|BTCmove|  momentum-tilt(EV/sh, n)   verdict")
for h in range(24):
    r = H.get(h)
    if not r or r["n"] < 3:
        continue
    revr = 100*r["rev"]/r["n"]
    avgmom = statistics.mean(r["mom"]) if r["mom"] else 0
    tn = len(r["tilt"]); tev = (sum(t/5 for t in r["tilt"])/tn) if tn else 0
    verdict = "GOOD trend" if revr < 25 else ("ok" if revr < 40 else "CHOPPY bad")
    print("%02d  %3d   %5.0f%%    $%6.0f      %+.3f (n%d)            %s"
          % (h, r["n"], revr, avgmom, tev, tn, verdict))
print()
print("low reversal% + high BTC move = trending hour (strategy works).")
print("high reversal% = choppy hour (naked catches loser = our live losses).")
print("competitor trades ~15-17 UTC; check if those are the low-reversal hours.")
