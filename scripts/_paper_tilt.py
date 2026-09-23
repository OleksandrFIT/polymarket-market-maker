"""PAPER tilt simulator on REAL 15m windows — zero real money.

Runs ALONGSIDE the live balanced bot (which trades pairs for real). Here we only
SIMULATE a momentum-driven directional tilt and score it on real prices, so we
learn whether adding a tilt is worth it BEFORE risking a cent.

Tilt rule: at a decision minute, if BTC has moved > THR in a direction since the
window open, BUY tilt_size of the favored (momentum) side at its real Polymarket
price then; hold to resolution. Paper PnL = size*(1-price) if favored wins else
-size*price. Reports total, hit-rate, EV/share, and time-thirds (robustness).

Uses prices-history per-minute Up path + winner. Caches per-window under
/tmp/poly_path15_cache. Read-only.
"""
import urllib.request, json, os, time, statistics

UA = {"User-Agent": "Mozilla/5.0"}
PCACHE = "/tmp/poly_path15_cache"
os.makedirs(PCACHE, exist_ok=True)
STEP = 900
END_TS = 1781631000          # a recent resolved 15m window (ts % 900 == 0)
N_WANT = 400
TILT_SIZE = 5                # paper shares per tilt


def _get(url, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.7)


def up_token(ts):
    r = _get("https://gamma-api.polymarket.com/markets?slug=btc-updown-15m-%d&closed=true" % ts)
    if not isinstance(r, list) or not r:
        return None
    toks = r[0].get("clobTokenIds")
    if isinstance(toks, str):
        toks = json.loads(toks)
    return toks[0] if toks else None


def path(ts):
    """per-MINUTE Up price [0..14] + winner. cached. None if no data."""
    fn = os.path.join(PCACHE, "%d.json" % ts)
    if os.path.exists(fn):
        d = json.load(open(fn))
        return d if d else None
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
    json.dump(d, open(fn, "w"))
    return d


def btc_closes(lo, hi):
    closes = {}
    end = hi * 1000
    while True:
        kl = _get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000&endTime=%d" % end)
        if not kl:
            break
        for k in kl:
            closes[k[0] // 1000] = float(k[4])
        end = kl[0][0] - 60000
        if kl[0][0] // 1000 <= lo:
            break
    return closes


print("loading up to %d 15m windows (cached)..." % N_WANT)
wins = []
ts = END_TS
miss = 0
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
    time.sleep(0.03)
wins.sort(key=lambda w: w["ts"])
n = len(wins)
if n:
    span = (wins[-1]["ts"] - wins[0]["ts"]) / 3600
    print("loaded %d windows over %.1f hours\n" % (n, span))
closes = btc_closes(wins[0]["ts"] - 120, wins[-1]["ts"] + STEP) if n else {}


def btc_at(t):
    for d in (0, 60):
        if (t - d) in closes:
            return closes[t - d]
    return None


def tilt_trades(ws, dmin, thr):
    """paper tilt at decision minute dmin, BTC move threshold thr."""
    tr = []
    for w in ws:
        b_now = btc_at(w["ts"] + 60 * dmin); b_open = btc_at(w["ts"])
        if b_now is None or b_open is None:
            continue
        mom = b_now - b_open
        if abs(mom) < thr:
            continue                       # no signal -> no tilt this window
        side = "Up" if mom > 0 else "Down"
        price = w["up"][dmin] if side == "Up" else 1 - w["up"][dmin]
        won = (side == w["winner"])
        tr.append((price, won))
    return tr


def ev(tr):
    if not tr:
        return (0, 0.0, 0.0, 0.0)
    hr = sum(1 for _, x in tr if x) / len(tr)
    cost = statistics.mean(p for p, _ in tr)
    return (len(tr), hr, cost, hr - cost)


print("=== PAPER TILT — momentum-driven, %d-share, EV per share ===" % TILT_SIZE)
print("%-22s %-18s %-18s %-18s" % ("rule", "ALL", "early3", "late3"))
third = n // 3
for dmin in (7, 10, 12):
    for thr in (20, 40):
        cells = []
        for ws in (wins, wins[:third], wins[2*third:]):
            nn, hr, cost, e = ev(tilt_trades(ws, dmin, thr))
            cells.append("n%d h%.0f%% EV%+.3f" % (nn, 100*hr, e) if nn else "—")
        # paper $ over ALL (per window incl. no-signal sit-outs)
        allt = tilt_trades(wins, dmin, thr)
        pnl = sum(TILT_SIZE*(1-p) if w else -TILT_SIZE*p for p, w in allt)
        print("min%d move>$%-3d        %-18s %-18s %-18s  paper $%+.1f/%d win" %
              (dmin, thr, cells[0], cells[1], cells[2], pnl, len(allt)))
print()
print("ROBUST +EV (positive EV/share in ALL thirds) => tilt worth adding.")
print("EV/share <=0 or flips across thirds => skip the tilt, stay balanced.")
