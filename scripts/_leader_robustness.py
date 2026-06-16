"""Large-sample, out-of-sample test of the 'strong leader is underpriced' edge.

Uses prices-history (1 call/window, per-minute) over MANY windows across days,
split into chronological thirds to check the edge isn't one-period luck.

For each window: per-minute Up price path + final->winner. Test buying the
strong favorite (price>thr at minute m), hold to resolution. EV = hit - cost.
Caches per-window path under /tmp/poly_path_cache.
"""
import urllib.request, json, os, time, statistics

UA = {"User-Agent": "Mozilla/5.0"}
PCACHE = "/tmp/poly_path_cache"
os.makedirs(PCACHE, exist_ok=True)
SPREAD = 0.01
END_TS = 1781461800
N_WANT = 1500


def _get(url, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.8)


def up_token(ts):
    r = _get("https://gamma-api.polymarket.com/markets?slug=btc-updown-5m-%d&closed=true" % ts)
    if not isinstance(r, list) or not r:
        return None
    toks = r[0].get("clobTokenIds")
    if isinstance(toks, str):
        toks = json.loads(toks)
    return toks[0] if toks else None


def path(ts):
    """per-minute Up price [0..4] + winner from final price. cached. None if no data."""
    fn = os.path.join(PCACHE, "%d.json" % ts)
    if os.path.exists(fn):
        d = json.load(open(fn))
        return d if d else None
    tok = up_token(ts)
    if not tok:
        json.dump(None, open(fn, "w")); return None
    r = _get("https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=1"
             % (tok, ts - 30, ts + 330))
    h = r.get("history", []) if isinstance(r, dict) else []
    if len(h) < 3:
        json.dump(None, open(fn, "w")); return None
    px = [None] * 5
    for pt in h:
        m = int((pt["t"] - ts) // 60)
        if 0 <= m < 5:
            px[m] = pt["p"]
    last = h[0]["p"]
    for m in range(5):
        if px[m] is None:
            px[m] = last
        last = px[m]
    final = h[-1]["p"]
    winner = "Up" if final >= 0.5 else "Down"
    d = {"up": px, "winner": winner, "ts": ts}
    json.dump(d, open(fn, "w"))
    return d


print("loading up to %d windows via prices-history (cached)..." % N_WANT)
wins = []
ts = END_TS
miss = 0
while len(wins) < N_WANT and miss < 120:
    try:
        d = path(ts)
    except Exception:
        d = None
    if d:
        wins.append(d); miss = 0
    else:
        miss += 1
    ts -= 300
    time.sleep(0.03)
wins.sort(key=lambda w: w["ts"])
n = len(wins)
span_h = (wins[-1]["ts"] - wins[0]["ts"]) / 3600 if n > 1 else 0
print("loaded %d windows spanning %.1f hours\n" % (n, span_h))


def sp(w, m, side):
    return w["up"][m] if side == "Up" else 1.0 - w["up"][m]


def regime(w):
    """trend = Up price stayed one side of 0.5 (<=1 crossing); chop = oscillated (>=2)."""
    cr = 0
    for i in range(1, 5):
        if (w["up"][i] - 0.5) * (w["up"][i-1] - 0.5) < 0:
            cr += 1
    return "trend" if cr <= 1 else "chop"


def test(ws, m, thr):
    tr = []
    for w in ws:
        up = w["up"][m]
        side = "Up" if up > 0.5 else "Down"
        p = sp(w, m, side)
        if p > thr:
            tr.append((p + SPREAD, side == w["winner"]))
    if not tr:
        return None
    hr = sum(1 for _, x in tr if x) / len(tr)
    cost = statistics.mean(c for c, _ in tr)
    return len(tr), hr, cost, hr - cost


# chronological thirds for out-of-sample
third = n // 3
parts = [("ALL", wins), ("early3", wins[:third]), ("mid3", wins[third:2*third]), ("late3", wins[2*third:])]

for m in (2, 3):
    print("=== STRONG LEADER @ minute %d (buy favorite if price>thr) ===" % m)
    print("%-8s %16s %16s %16s" % ("thr", "ALL", "early3", "late3"))
    for thr in (0.60, 0.70, 0.80):
        cells = []
        for label, ws in [("ALL", wins), ("early3", wins[:third]), ("late3", wins[2*third:])]:
            r = test(ws, m, thr)
            cells.append("n%d h%.0f%% EV%+.3f" % (r[0], 100*r[1], r[3]) if r else "—")
        print("%-8.2f %16s %16s %16s" % (thr, cells[0], cells[1], cells[2]))
    print()

# also a flat "buy leader@2 always" baseline across thirds
print("=== baseline leader@2 (any favorite>0.5) across thirds ===")
for label, ws in parts:
    tr = [(sp(w, 2, "Up" if w["up"][2] > 0.5 else "Down") + SPREAD,
           ("Up" if w["up"][2] > 0.5 else "Down") == w["winner"]) for w in ws]
    hr = sum(1 for _, x in tr if x)/len(tr); cost = statistics.mean(c for c, _ in tr)
    print(" %-7s n=%4d hit=%.1f%% cost=%.3f EV=$%+.4f/sh" % (label, len(tr), 100*hr, cost, hr-cost))
print()
print("ROBUST +EV = positive in ALL thirds, not just one. If it flips sign across")
print("thirds -> it's regime luck, not a real edge.")
print()
print("=== REGIME SPLIT: strong leader >0.60 @ minute 2, trend vs chop ===")
tw_ = [w for w in wins if regime(w) == "trend"]
ch_ = [w for w in wins if regime(w) == "chop"]
print(" overall: trend=%d (%.0f%%)  chop=%d (%.0f%%)" % (len(tw_), 100*len(tw_)/n, len(ch_), 100*len(ch_)/n))
for label, ws in [("TREND", tw_), ("CHOP", ch_)]:
    r = test(ws, 2, 0.60)
    if r:
        print(" %-6s n=%4d hit=%.1f%% cost=%.3f EV=$%+.4f/sh %s" %
              (label, r[0], 100*r[1], r[2], r[3], "<<< +EV" if r[3] > 0.005 else "<<< NOT +EV"))
print()
print("DECISIVE: if CHOP is also +EV -> robust real edge. If CHOP is -EV -> the")
print("edge is trend-only (dangerous: a choppy week would lose).")
