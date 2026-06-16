"""Final test: does a BTC-momentum FILTER select the +EV trend windows and avoid
the -EV chop windows for the 'buy the favorite @ minute 2' trade?

Mechanism (the only reason this could beat efficiency): if Polymarket's price
lags BTC, then strong BTC momentum in the favorite's direction flags a window
that will keep trending (favorite holds) -> harvest +EV, skip the choppy ones.

Uses cached per-minute Up paths (winner) + Binance 1m closes for BTC momentum.
Read-only.
"""
import urllib.request, json, os, statistics

UA = {"User-Agent": "Mozilla/5.0"}
PCACHE = "/tmp/poly_path_cache"
SPREAD = 0.01
END_TS = 1781461800
N = 1500


def _get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))


# load cached poly paths
wins = []
ts = END_TS
miss = 0
while len(wins) < N and miss < 200:
    fn = os.path.join(PCACHE, "%d.json" % ts)
    if os.path.exists(fn):
        d = json.load(open(fn))
        if d:
            wins.append(d)
        else:
            miss += 1
    else:
        miss += 1
    ts -= 300
wins.sort(key=lambda w: w["ts"])
n = len(wins)
print("loaded %d cached poly windows" % n)

# fetch Binance 1m closes covering the range
lo = wins[0]["ts"] - 120
hi = wins[-1]["ts"] + 360
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
print("fetched %d BTC 1m closes\n" % len(closes))


def btc_at(t):
    """BTC close at/just before minute boundary t."""
    for d in range(0, 120, 60):
        if (t - d) in closes:
            return closes[t - d]
    return None


def regime(w):
    cr = 0
    for i in range(1, 5):
        if (w["up"][i] - 0.5) * (w["up"][i-1] - 0.5) < 0:
            cr += 1
    return "trend" if cr <= 1 else "chop"


M = 2
THR = 0.60
results = {}   # label -> list of (cost, won)
for tag in ("all_fav", "aligned", "strong_aligned"):
    results[tag] = []
skipped_chop = {"all_fav": 0, "aligned": 0, "strong_aligned": 0}
chop_count = aligned_chop = strong_chop = 0

for w in wins:
    upm = w["up"][M]
    if max(upm, 1 - upm) <= THR:
        continue
    side = "Up" if upm > 0.5 else "Down"
    cost = (upm if side == "Up" else 1 - upm) + SPREAD
    won = (side == w["winner"])
    t0 = w["ts"]
    b_now = btc_at(t0 + 60 * M)
    b_prev = btc_at(t0 + 60 * (M - 1))
    b_open = btc_at(t0)
    if b_now is None or b_open is None:
        continue
    mom = b_now - b_open          # BTC move over the window so far
    rg = regime(w)
    results["all_fav"].append((cost, won))
    aligned = (mom > 0 and side == "Up") or (mom < 0 and side == "Down")
    if aligned:
        results["aligned"].append((cost, won))
        # "strong": BTC moved > $20 over the window so far in the favorite's dir
        if abs(mom) > 20:
            results["strong_aligned"].append((cost, won))


def summ(tr):
    if not tr:
        return "(none)"
    hr = sum(1 for _, x in tr if x) / len(tr)
    c = statistics.mean(c for c, _ in tr)
    return "n=%4d hit=%.1f%% cost=$%.3f EV=$%+.4f/sh %s" % (
        len(tr), 100*hr, c, hr - c, "<<< +EV" if hr - c > 0.005 else "")


print("=== BTC-momentum filter on 'buy favorite >0.60 @ minute 2' ===")
print(" no filter (all favorites):        %s" % summ(results["all_fav"]))
print(" aligned (BTC moving fav's way):   %s" % summ(results["aligned"]))
print(" strong+aligned (|BTC move|>$20):  %s" % summ(results["strong_aligned"]))
print()
# does the strong+aligned filter avoid chop? check regime mix of selected trades
sel = []
for w in wins:
    upm = w["up"][M]
    if max(upm, 1-upm) <= THR:
        continue
    side = "Up" if upm > 0.5 else "Down"
    b_now = btc_at(w["ts"]+60*M); b_open = btc_at(w["ts"])
    if b_now is None or b_open is None:
        continue
    mom = b_now - b_open
    aligned = (mom > 0 and side == "Up") or (mom < 0 and side == "Down")
    if aligned and abs(mom) > 20:
        sel.append(regime(w))
if sel:
    print(" strong+aligned selection regime mix: trend=%.0f%% chop=%.0f%% (n=%d)" %
          (100*sel.count("trend")/len(sel), 100*sel.count("chop")/len(sel), len(sel)))
print()
print("DECISIVE: if strong+aligned is robustly +EV AND mostly avoids chop -> a real,")
print("harvestable edge. If filtering doesn't lift EV above ~0 -> market efficient, done.")
print()

def trades_for(ws, mom_thr):
    out = []
    for w in ws:
        upm = w["up"][M]
        if max(upm, 1-upm) <= THR:
            continue
        side = "Up" if upm > 0.5 else "Down"
        cost = (upm if side == "Up" else 1-upm) + SPREAD
        b_now = btc_at(w["ts"]+60*M); b_open = btc_at(w["ts"])
        if b_now is None or b_open is None:
            continue
        mom = b_now - b_open
        aligned = (mom > 0 and side == "Up") or (mom < 0 and side == "Down")
        if aligned and abs(mom) > mom_thr:
            out.append((cost, side == w["winner"]))
    return out

def ev(tr):
    if not tr:
        return (0, 0, 0)
    hr = sum(1 for _, x in tr if x)/len(tr); c = statistics.mean(c for c, _ in tr)
    return (len(tr), hr-c, hr)

third = n // 3
slices = [("early3", wins[:third]), ("mid3", wins[third:2*third]), ("late3", wins[2*third:])]

print("=== THRESHOLD SWEEP x TIME-THIRDS (strong+aligned) ===")
print("%-10s %18s %18s %18s" % ("BTC move>", "early3", "mid3", "late3"))
for mt in (10, 20, 30, 40):
    cells = []
    for _, ws in slices:
        nn, e, h = ev(trades_for(ws, mt))
        cells.append("n%d EV%+.3f" % (nn, e) if nn else "—")
    print("$%-9d %18s %18s %18s" % (mt, cells[0], cells[1], cells[2]))
print()
print("ROBUST = +EV in ALL three thirds at a stable threshold. If it flips sign")
print("across thirds (like the unfiltered leader did) -> still regime luck.")
