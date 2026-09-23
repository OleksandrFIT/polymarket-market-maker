"""EXACT forensic of wallet 0xb945...db68 — definitive winner (gamma on-chain
resolution) + PnL bucketed by RESOLUTION time (как the Polymarket profit graph).

Goal: reproduce the graph's hourly increment (user saw +$179: $273->$452 in "14-15")
and pin the timezone. 0 sells confirmed, so PnL = winning_shares - spent. Read-only.
"""
import urllib.request, json, time, collections, datetime as dt

WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
UA = {"User-Agent": "Mozilla/5.0"}
NOW = int(time.time())
SINCE = NOW - 28 * 3600
TZ = {"UTC": 0, "ET": -4 * 3600, "Kyiv": 3 * 3600}


def get(u, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.4)


# ---- trades ----
trades, off = [], 0
while True:
    b = get("https://data-api.polymarket.com/trades?user=%s&limit=500&offset=%d" % (WALLET, off))
    if not b:
        break
    keep = [t for t in b if t.get("timestamp", 0) >= SINCE and t.get("side") == "BUY"]
    trades += keep
    if len([t for t in b if t.get("timestamp", 0) >= SINCE]) < len(b) or len(b) < 500:
        break
    off += 500
    time.sleep(0.1)

W = collections.defaultdict(lambda: {"ts": 0, "slug": "", "up_sh": 0.0, "up_usd": 0.0,
                                     "dn_sh": 0.0, "dn_usd": 0.0})
for t in trades:
    w = W[t["conditionId"]]
    w["slug"] = t.get("slug", "")
    try:
        w["ts"] = int(t["slug"].split("-")[-1])
    except Exception:
        pass
    sz, px = float(t["size"]), float(t["price"])
    if t.get("outcomeIndex") == 0:
        w["up_sh"] += sz; w["up_usd"] += sz * px
    else:
        w["dn_sh"] += sz; w["dn_usd"] += sz * px
print("windows: %d (from %d BUY trades)" % (len(W), len(trades)))


def winner_gamma(slug):
    """Definitive resolved outcome via gamma. Returns 'Up'|'Dn'|None(unresolved)."""
    r = get("https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % slug)
    if not isinstance(r, list) or not r:
        return None
    m = r[0]
    if not (m.get("closed") or m.get("umaResolutionStatus") == "resolved"):
        # still allow if outcomePrices already 1/0
        pass
    op = m.get("outcomePrices")
    if isinstance(op, str):
        try:
            op = json.loads(op)
        except Exception:
            op = None
    if not op or len(op) < 2:
        return None
    try:
        p0, p1 = float(op[0]), float(op[1])
    except Exception:
        return None
    if p0 >= 0.99:
        return "Up"
    if p1 >= 0.99:
        return "Dn"
    return None   # not definitively resolved yet


rows = []
unresolved = 0
for c, w in W.items():
    if not w["ts"]:
        continue
    tf = 300 if "-5m-" in w["slug"] else 900
    res_ts = w["ts"] + tf
    win = winner_gamma(w["slug"])
    if win is None:
        unresolved += 1
        continue
    spent = w["up_usd"] + w["dn_usd"]
    win_sh = w["up_sh"] if win == "Up" else w["dn_sh"]
    rows.append({"res_ts": res_ts, "ts": w["ts"], "pnl": win_sh - spent, "spent": spent, "win": win})
rows.sort(key=lambda r: r["res_ts"])
tot = sum(r["pnl"] for r in rows)
print("resolved: %d | unresolved/open: %d | TOTAL realized PnL (by resolution): $%+.1f\n"
      % (len(rows), unresolved, tot))

# ---- by-hour by RESOLUTION time, 3 timezones ----
for name, off in TZ.items():
    byhr = collections.defaultdict(lambda: [0, 0.0])
    for r in rows:
        h = (r["res_ts"] + off) // 3600 % 24
        byhr[h][0] += 1; byhr[h][1] += r["pnl"]
    print("=== PnL by RESOLUTION hour — %s ===" % name)
    for h in sorted(byhr):
        n, p = byhr[h]
        flag = "  <<< ~+$179?" if 150 <= p <= 210 else ""
        print("  %02d:00  n%-3d  $%+8.1f%s" % (h, n, p, flag))
    print()

# ---- cumulative curve (hourly, Kyiv) to see it climb like the graph ----
print("=== cumulative realized curve (Kyiv resolution hour) ===")
cum = 0.0
byhr_k = collections.defaultdict(float)
for r in rows:
    h = (r["res_ts"] + TZ["Kyiv"]) // 3600 % 24
    byhr_k[h] += r["pnl"]
for h in sorted(byhr_k):
    start = cum
    cum += byhr_k[h]
    print("  Kyiv %02d:00  %+7.1f   cum $%.0f -> $%.0f" % (h, byhr_k[h], start, cum))
