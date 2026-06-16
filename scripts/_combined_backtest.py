"""COMBINED bot backtest on real data: the improved merge-maker.

Per window, decide regime by BTC momentum at minute 2:
  CHOP  (|BTC move| <= $20): run pair-collection (catch both legs if both dip
        <=0.49 -> pair edge; if only one dips -> naked rides to resolution).
  TREND (|BTC move| >  $20): momentum mode — if favorite price>0.60 AND BTC moved
        in the favorite's direction -> buy favorite @ minute2 price, hold; do NOT
        catch the loser. else sit.

Reports net/window, by regime, and across time-thirds (robustness).
Uses cached per-minute Up paths (winner) + Binance 1m closes. Read-only.
"""
import urllib.request, json, os, statistics

UA = {"User-Agent": "Mozilla/5.0"}
PCACHE = "/tmp/poly_path_cache"
BCACHE = "/tmp/btc_closes.json"
SPREAD = 0.01
BID1, BID2 = 0.49, 0.46
BTC_THR = 20.0
FAV_MIN = 0.60
M = 2
END_TS = 1781461800
N = 1500


def _get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))


def my_fill(low):
    if low <= BID2:
        return BID2
    if low <= BID1:
        return BID1
    return None


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

# BTC closes (cache)
if os.path.exists(BCACHE):
    closes = {int(k): v for k, v in json.load(open(BCACHE)).items()}
else:
    closes = {}
    lo = wins[0]["ts"] - 120
    end = (wins[-1]["ts"] + 360) * 1000
    while True:
        kl = _get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000&endTime=%d" % end)
        if not kl:
            break
        for k in kl:
            closes[k[0] // 1000] = float(k[4])
        end = kl[0][0] - 60000
        if kl[0][0] // 1000 <= lo:
            break
    json.dump(closes, open(BCACHE, "w"))


def btc_at(t):
    for d in (0, 60):
        if (t - d) in closes:
            return closes[t - d]
    return None


def minprice(w, side):
    return min(w["up"][m] if side == "Up" else 1 - w["up"][m] for m in range(5))


def window_pnl(w):
    b_now = btc_at(w["ts"] + 60 * M)
    b_open = btc_at(w["ts"])
    if b_now is None or b_open is None:
        return None, "skip"
    mom = b_now - b_open
    winner = w["winner"]
    if abs(mom) > BTC_THR:                                   # TREND -> momentum
        side = "Up" if w["up"][M] > 0.5 else "Down"
        favp = w["up"][M] if side == "Up" else 1 - w["up"][M]
        aligned = (mom > 0 and side == "Up") or (mom < 0 and side == "Down")
        if favp > FAV_MIN and aligned:
            cost = favp + SPREAD
            return 5 * (1.0 if side == winner else 0.0) - 5 * cost, "momentum"
        return 0.0, "trend_sit"
    # CHOP -> pair-collection
    lu, ld = minprice(w, "Up"), minprice(w, "Down")
    cu, cd = my_fill(lu), my_fill(ld)
    if cu and cd:                                            # both dipped -> pair
        return 5 * (1.0 - cu - cd), "pair"
    if cu or cd:                                             # one dipped -> FLATTEN (no pair formed)
        side = "Up" if cu else "Down"
        f = cu or cd
        mk = w["up"][3] if side == "Up" else 1 - w["up"][3]  # flatten mark @ min3
        return 5 * ((mk - SPREAD) - f), "chop_flat"
    return 0.0, "sit"


res = []
cases = {}
for w in wins:
    p, c = window_pnl(w)
    if p is None:
        continue
    res.append((p, c, w["ts"]))
    cases[c] = cases.get(c, 0) + 1

pnls = [p for p, _, _ in res]
m = len(pnls)
print("=== COMBINED IMPROVED BOT — %d real windows ===" % m)
print("BTC_THR=$%.0f  FAV_MIN=%.2f  entry@min%d  5-share" % (BTC_THR, FAV_MIN, M))
print()
print("net $%+.2f | /win $%+.3f | std %.2f | worst $%+.2f | win<-$2: %d" %
      (sum(pnls), sum(pnls)/m, statistics.pstdev(pnls), min(pnls), sum(1 for x in pnls if x < -2)))
print()
print("case mix:", {k: "%d(%.0f%%)" % (v, 100*v/m) for k, v in sorted(cases.items())})
print()
# per-case contribution
print("per-case avg PnL:")
for c in sorted(cases):
    cp = [p for p, cc, _ in res if cc == c]
    print("  %-12s n=%4d avg $%+.3f total $%+.2f" % (c, len(cp), statistics.mean(cp), sum(cp)))
print()
# robustness: time-thirds
res.sort(key=lambda r: r[2])
third = m // 3
for label, sl in [("early3", res[:third]), ("mid3", res[third:2*third]), ("late3", res[2*third:])]:
    pp = [p for p, _, _ in sl]
    print(" %-7s n=%4d /win $%+.3f net $%+.2f" % (label, len(pp), sum(pp)/len(pp), sum(pp)))
print()
print("GOAL: net /win POSITIVE in ALL thirds = robust improved bot. Compare to")
print("the pure ladder's real −$0.51/win. If combined is +, we have our bot.")
