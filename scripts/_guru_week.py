"""FULL week of the guru's trades, bypassing the data-api 3500 offset cap by
fetching PER MARKET (?user=&market={conditionId}). Exact PnL via the resolved
outcome (he never sells: PnL = winning-side shares - total spent). Cached+resumable.
"""
import urllib.request, json, os, time, collections

UA = {"User-Agent": "Mozilla/5.0"}
ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
CACHE = "/tmp/poly_guru_week"
os.makedirs(CACHE, exist_ok=True)
STEP = 900
DAYS = 7


def get(u, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.5)


def window(ts):
    """cached per-window: his Up/Down shares+cost + winner, or None if he didn't trade."""
    fn = os.path.join(CACHE, "%d.json" % ts)
    if os.path.exists(fn):
        return json.load(open(fn))
    slug = "btc-updown-15m-%d" % ts
    m = get("https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % slug)
    if not m:
        json.dump(None, open(fn, "w")); return None
    mm = m[0]
    cid = mm.get("conditionId")
    op = mm.get("outcomePrices")
    if isinstance(op, str):
        op = json.loads(op)
    if not cid or not op:
        json.dump(None, open(fn, "w")); return None
    winner = "Up" if float(op[0]) > 0.5 else "Down"
    tr, off = [], 0
    while True:
        r = get("https://data-api.polymarket.com/trades?user=%s&market=%s&limit=500&offset=%d" % (ADDR, cid, off))
        if not isinstance(r, list) or not r:
            break
        tr += r; off += len(r)
        if len(r) < 500:
            break
    d = {"Up": [0.0, 0.0], "Down": [0.0, 0.0], "winner": winner, "ts": ts, "n": len(tr)}
    for t in tr:
        if t.get("side") != "BUY":
            continue
        oc = t.get("outcome")
        if oc in ("Up", "Down"):
            d[oc][0] += float(t.get("size") or 0)
            d[oc][1] += float(t.get("price") or 0) * float(t.get("size") or 0)
    json.dump(d, open(fn, "w"))
    return d


now = int(time.time())
end = (now // STEP) * STEP
start = end - DAYS * 86400
print("fetching %d windows over %d days (cached)..." % ((end - start) // STEP, DAYS), flush=True)

wins = []
ts = start
done = 0
while ts < end:
    d = window(ts)
    done += 1
    if done % 60 == 0:
        print("  ...%d windows scanned, %d traded" % (done, len(wins)), flush=True)
    if d and (d["Up"][0] + d["Down"][0]) >= 5:
        wins.append(d)
    ts += STEP
    time.sleep(0.03)

print("\n=== GURU — FULL WEEK (%d traded windows) ===" % len(wins))
tot_pnl = 0.0; tot_buy = 0.0; w_count = 0; l_count = 0
big_win = []; big_loss = []
for d in wins:
    uq, uc = d["Up"]; dq, dc = d["Down"]
    spent = uc + dc
    win_sh = uq if d["winner"] == "Up" else dq
    pnl = win_sh - spent
    tot_pnl += pnl; tot_buy += spent
    if pnl > 0: w_count += 1
    else: l_count += 1
    big_win.append((pnl, d));
big_win.sort(key=lambda x: x[0])
print("total BUY volume : $%.0f" % tot_buy)
print("NET PnL (7d)     : $%+.0f   (%.1f%% of volume)" % (tot_pnl, 100 * tot_pnl / tot_buy if tot_buy else 0))
print("windows          : %d   win %d (%.0f%%) / lose %d" % (len(wins), w_count, 100 * w_count / len(wins), l_count))
print("avg/window       : buy $%.0f   PnL $%+.1f" % (tot_buy / len(wins), tot_pnl / len(wins)))
print("\nTOP 5 wins:")
for pnl, d in big_win[-5:][::-1]:
    print("  %s  PnL $%+.0f  buy $%.0f  winner=%s" % (time.strftime("%m-%d %H:%M", time.gmtime(d["ts"])), pnl, d["Up"][1] + d["Down"][1], d["winner"]))
print("TOP 5 losses:")
for pnl, d in big_win[:5]:
    print("  %s  PnL $%+.0f  buy $%.0f  winner=%s" % (time.strftime("%m-%d %H:%M", time.gmtime(d["ts"])), pnl, d["Up"][1] + d["Down"][1], d["winner"]))
