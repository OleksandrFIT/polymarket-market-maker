"""Test the NEW-bot tactic ("early drift") on 5m BTC up/down windows.

At decision minute K (early: 1/2/3), bet the side that's AHEAD (mid>0.5) at its price,
hold to resolution. Edge = win-rate(ahead side) - entry price. Also EV/share with a
taker fee, and a breakdown by entry-price bucket (is being MORE ahead a stronger edge?).
Causal: decision uses only price up to minute K; winner is the settle. Read-only.
"""
import urllib.request, json, os, time, statistics, collections, datetime as dt

UA = {"User-Agent": "Mozilla/5.0"}
PCACHE = "/tmp/poly_path5_cache"
os.makedirs(PCACHE, exist_ok=True)
STEP = 300
N_WANT = 4500
FEE = 0.02
NOW = int(time.time())
END_TS = (NOW // STEP) * STEP - STEP


def get(u, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.6)


def up_token(ts):
    r = get("https://gamma-api.polymarket.com/markets?slug=btc-updown-5m-%d&closed=true" % ts)
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
            % (tok, ts - 20, ts + STEP + 20))
    h = r.get("history", []) if isinstance(r, dict) else []
    if len(h) < 4:
        json.dump(None, open(fn, "w")); return None
    px = [None] * 6
    for pt in h:
        m = int((pt["t"] - ts) // 60)
        if 0 <= m < 6:
            px[m] = pt["p"]
    last = h[0]["p"]
    for m in range(6):
        if px[m] is None:
            px[m] = last
        last = px[m]
    d = {"up": px, "winner": "Up" if h[-1]["p"] >= 0.5 else "Down", "ts": ts}
    json.dump(d, open(fn, "w")); return d


print("fetching up to %d recent 5m windows ending %s UTC..." %
      (N_WANT, dt.datetime.utcfromtimestamp(END_TS).strftime("%m-%d %H:%M")))
wins = []; ts = END_TS; miss = 0
while len(wins) < N_WANT and miss < 60:
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
n = len(wins)
print("loaded %d 5m windows over %.1f days\n" % (n, (wins[0]["ts"] - wins[-1]["ts"]) / 86400 if n else 0))

# overall reversal (min1 leader flips by settle) for context
def test(K):
    rows = []
    for w in wins:
        up = w["up"][K]
        side = "Up" if up > 0.5 else "Down"
        entry = up if side == "Up" else 1 - up
        won = (w["winner"] == side)
        rows.append((entry, won))
    n = len(rows)
    wr = sum(1 for e, won in rows if won) / n
    avg = statistics.mean(e for e, _ in rows)
    ev0 = wr - avg
    ev = statistics.mean((1 - (e + FEE)) if won else -(e + FEE) for e, won in rows)
    return n, wr, avg, ev0, ev, rows


print("=== EARLY-DRIFT: bet the side ahead at minute K, hold to settle ===")
print(" K(min)   n     winrate  avgEntry  edge(wr-entry)  EV/share@2c")
best = None
for K in (1, 2, 3):
    n, wr, avg, ev0, ev, rows = test(K)
    print("  %d      %4d    %5.1f%%   %.3f    %+.3f         %+.4f" % (K, n, 100*wr, avg, ev0, ev))
    if best is None or ev > best[0]:
        best = (ev, K, rows)

# edge by entry-price bucket at the best K (is being MORE ahead a stronger signal?)
ev, K, rows = best
print("\n=== at K=%d: edge by how-far-ahead (entry-price bucket) ===" % K)
print(" entry bucket   n     winrate  edge(wr-mid)  EV/share@2c")
buck = collections.defaultdict(list)
for e, won in rows:
    buck[int(e * 20) / 20.0].append((e, won))
for b in sorted(buck):
    g = buck[b]
    if len(g) < 20:
        continue
    wr = sum(1 for _, won in g if won) / len(g)
    mid = b + 0.025
    evb = statistics.mean((1 - (e + FEE)) if won else -(e + FEE) for e, won in g)
    print("  %.2f-%.2f   %4d    %5.1f%%   %+.3f        %+.4f" % (b, b + 0.05, len(g), 100*wr, wr - mid, evb))
print("\nedge>0 (winrate > entry+fee) => the early leader is underpriced => tactic +EV.")

# ---- MAKER-EV (no spread cost) + weekly stability (is it decaying?) ----
# maker approximation: post a bid at the leader's price, fill ~at entry -> EV = winrate - entry.
Kd = 2  # decision minute for the stability check
seq = []
for w in sorted(wins, key=lambda x: x["ts"]):
    up = w["up"][Kd]; side = "Up" if up > 0.5 else "Down"
    entry = up if side == "Up" else 1 - up
    won = (w["winner"] == side)
    seq.append((w["ts"], (1 - entry) if won else -entry))   # maker EV (0 spread)
mev = statistics.mean(e for _, e in seq)
print("\n=== MAKER-EV (0 spread) at K=%d: %+.4f/share (n%d) ===" % (Kd, mev, len(seq)))
t0 = seq[0][0]
wk = collections.defaultdict(list)
for ts, e in seq:
    wk[(ts - t0) // (7 * 86400)].append(e)
print("weekly maker-EV (decay check):")
for k in sorted(wk):
    d0 = dt.datetime.utcfromtimestamp(t0 + k * 7 * 86400).strftime("%m-%d")
    print("  week %d (from %s): n%-4d  maker-EV %+.4f" % (k + 1, d0, len(wk[k]), statistics.mean(wk[k])))
half = len(seq) // 2
print("  first half %+.4f | second half %+.4f (stable if similar)" %
      (statistics.mean(e for _, e in seq[:half]), statistics.mean(e for _, e in seq[half:])))
# realistic maker cost sensitivity: subtract a small effective cost
for cost in (0.0, 0.005, 0.01):
    ev = mev - cost
    print("  maker-EV minus %.1f¢ effective cost: %+.4f  %s" % (cost * 100, ev, "+EV" if ev > 0 else "-EV"))
