"""Today's realized PnL for a wallet (hold-to-resolution; 0 sells assumed).
Usage: python3 scripts/_today_pnl.py <address>
"""
import urllib.request, json, time, sys, collections, datetime as dt

ADDR = sys.argv[1] if len(sys.argv) > 1 else "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
UA = {"User-Agent": "Mozilla/5.0"}
NOW = int(time.time()); SINCE = NOW - 32 * 3600
KYIV = 3 * 3600


def get(u, tries=4):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.7)


trades, off = [], 0
while True:
    b = get("https://data-api.polymarket.com/trades?user=%s&limit=500&offset=%d" % (ADDR, off))
    if not isinstance(b, list) or not b:
        break
    keep = [t for t in b if t.get("timestamp", 0) >= SINCE]
    trades += keep
    if len(keep) < len(b) or len(b) < 500:
        break
    off += 500; time.sleep(0.15)
trades = [t for t in trades if t.get("side") == "BUY"]

W = collections.defaultdict(lambda: {"ts": 0, "slug": "", "sh": [0.0, 0.0], "usd": [0.0, 0.0]})
for t in trades:
    w = W[t["conditionId"]]; w["slug"] = t.get("slug", "")
    try: w["ts"] = int(t["slug"].split("-")[-1])
    except Exception: pass
    oi = t.get("outcomeIndex")
    if oi in (0, 1):
        w["sh"][oi] += float(t["size"]); w["usd"][oi] += float(t["size"]) * float(t["price"])


def winner(slug):
    r = get("https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % slug)
    if not isinstance(r, list) or not r: return None
    op = r[0].get("outcomePrices")
    if isinstance(op, str):
        try: op = json.loads(op)
        except Exception: return None
    if not op or len(op) < 2: return None
    if float(op[0]) >= 0.99: return 0
    if float(op[1]) >= 0.99: return 1
    return None


today_utc = dt.datetime.utcfromtimestamp(NOW).strftime("%Y-%m-%d")
today_kyiv = dt.datetime.utcfromtimestamp(NOW + KYIV).strftime("%Y-%m-%d")
rows = []
for c, w in W.items():
    if not w["ts"]: continue
    tf = 300 if "-5m-" in w["slug"] else 900
    win = winner(w["slug"])
    spent = w["usd"][0] + w["usd"][1]
    if win is None:
        rows.append({"ts": w["ts"], "tf": tf, "pnl": None, "spent": spent, "open": False}); continue
    pnl = w["sh"][win] - spent
    rows.append({"ts": w["ts"], "tf": tf, "pnl": pnl, "spent": spent, "open": True})

res = [r for r in rows if r["pnl"] is not None]
unres = [r for r in rows if r["pnl"] is None]

def day_sum(off, day):
    s = [r for r in res if dt.datetime.utcfromtimestamp(r["ts"] + off).strftime("%Y-%m-%d") == day]
    return sum(r["pnl"] for r in s), sum(r["spent"] for r in s), len(s)

pk, sk, nk = day_sum(KYIV, today_kyiv)
pu, su, nu = day_sum(0, today_utc)
print("wallet %s" % ADDR)
print("resolved windows (last 32h): %d | still-open/unresolved: %d\n" % (len(res), len(unres)))
print("=== TODAY (Kyiv %s): PnL $%+.1f on $%.0f spent (%d windows, %.1f%%)" %
      (today_kyiv, pk, sk, nk, 100 * pk / sk if sk else 0))
print("=== TODAY (UTC  %s): PnL $%+.1f on $%.0f spent (%d windows, %.1f%%)" %
      (today_utc, pu, su, nu, 100 * pu / su if su else 0))
print("=== last 32h total resolved: PnL $%+.1f on $%.0f spent" %
      (sum(r["pnl"] for r in res), sum(r["spent"] for r in res)))

# per Kyiv hour today
print("\nper Kyiv hour (today):")
byh = collections.defaultdict(lambda: [0, 0.0])
for r in res:
    if dt.datetime.utcfromtimestamp(r["ts"] + KYIV).strftime("%Y-%m-%d") == today_kyiv:
        h = (r["ts"] + KYIV) // 3600 % 24
        byh[h][0] += 1; byh[h][1] += r["pnl"]
for h in sorted(byh):
    print("  %02d:00  n%-3d  $%+7.1f" % (h, byh[h][0], byh[h][1]))
