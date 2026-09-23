"""Full bet-by-bet analysis of the wallet's PROFITABLE morning window
(Kyiv 10:00-13:59 by resolution time, the +$493 ramp). Definitive on-chain winner
(gamma closed=true), 0 sells confirmed -> PnL = winning_shares - spent. Read-only.
"""
import urllib.request, json, time, collections, statistics, datetime as dt

WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
UA = {"User-Agent": "Mozilla/5.0"}
NOW = int(time.time())
SINCE = NOW - 30 * 3600
KYIV = 3 * 3600
ET = -4 * 3600
HOURS = {10, 11, 12, 13}          # Kyiv resolution hours to analyze


def get(u, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.4)


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

W = collections.defaultdict(lambda: {"ts": 0, "slug": "", "fills": [],
                                     "up_sh": 0.0, "up_usd": 0.0, "dn_sh": 0.0, "dn_usd": 0.0})
for t in trades:
    w = W[t["conditionId"]]
    w["slug"] = t.get("slug", "")
    try:
        w["ts"] = int(t["slug"].split("-")[-1])
    except Exception:
        pass
    sz, px = float(t["size"]), float(t["price"])
    up = t.get("outcomeIndex") == 0
    w["fills"].append((t["timestamp"], "Up" if up else "Dn", sz, px))
    if up:
        w["up_sh"] += sz; w["up_usd"] += sz * px
    else:
        w["dn_sh"] += sz; w["dn_usd"] += sz * px


def winner(slug):
    r = get("https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % slug)
    if not isinstance(r, list) or not r:
        return None
    op = r[0].get("outcomePrices")
    if isinstance(op, str):
        try:
            op = json.loads(op)
        except Exception:
            return None
    if not op or len(op) < 2:
        return None
    if float(op[0]) >= 0.99:
        return "Up"
    if float(op[1]) >= 0.99:
        return "Dn"
    return None


def hh(ts, o):
    return dt.datetime.utcfromtimestamp(ts + o).strftime("%H:%M")


rows = []
for c, w in W.items():
    if not w["ts"]:
        continue
    tf = 300 if "-5m-" in w["slug"] else 900
    res_ts = w["ts"] + tf
    if (res_ts + KYIV) // 3600 % 24 not in HOURS:
        continue
    win = winner(w["slug"])
    if win is None:
        continue
    spent = w["up_usd"] + w["dn_usd"]
    win_sh = w["up_sh"] if win == "Up" else w["dn_sh"]
    pnl = win_sh - spent
    pairs = min(w["up_sh"], w["dn_sh"])
    up_avg = w["up_usd"] / w["up_sh"] if w["up_sh"] else 0
    dn_avg = w["dn_usd"] / w["dn_sh"] if w["dn_sh"] else 0
    pair_pnl = pairs * (1 - (up_avg + dn_avg))
    fav_avg = up_avg if win == "Up" else dn_avg
    heavy = "Up" if w["up_sh"] >= w["dn_sh"] else "Dn"
    rows.append({**w, "tf": "5m" if tf == 300 else "15m", "res_ts": res_ts, "win": win,
                 "spent": spent, "pnl": pnl, "pairs": pairs, "up_avg": up_avg, "dn_avg": dn_avg,
                 "pair_pnl": pair_pnl, "dir_pnl": pnl - pair_pnl, "fav_avg": fav_avg,
                 "heavy_is_winner": heavy == win})
rows.sort(key=lambda r: r["res_ts"])

tot = sum(r["pnl"] for r in rows)
print("=" * 70)
print("  WINNING WINDOW (Kyiv 10:00-13:59 by resolution) — %d windows, PnL $%+.1f" % (len(rows), tot))
print("=" * 70)
print("\n%-11s %-4s %-4s %6s %6s %7s  fav_entry  pair/dir" % ("open(Kyiv)", "tf", "win", "spent", "PnL", "winsh"))
for r in rows:
    print("%s/%s  %-4s %-4s %6.0f %+6.0f %7.0f   %.3f   %+.0f/%+.0f%s" % (
        hh(r["ts"], KYIV), hh(r["ts"], ET), r["tf"], r["win"], r["spent"], r["pnl"],
        r["up_sh"] if r["win"] == "Up" else r["dn_sh"], r["fav_avg"],
        r["pair_pnl"], r["dir_pnl"], "" if r["heavy_is_winner"] else "  (heavy=LOSER)"))

# ---- aggregates ----
n = len(rows)
won_dir = sum(1 for r in rows if r["heavy_is_winner"])
fav_entries = [r["fav_avg"] for r in rows if r["fav_avg"] > 0]
tf5 = [r for r in rows if r["tf"] == "5m"]; tf15 = [r for r in rows if r["tf"] == "15m"]
print("\n--- AGGREGATES (winning morning) ---")
print("windows: %d (5m %d / 15m %d) | total spent $%.0f | total PnL $%+.1f (%.1f%% of spend)"
      % (n, len(tf5), len(tf15), sum(r["spent"] for r in rows), tot, 100 * tot / sum(r["spent"] for r in rows)))
print("heavy side WON: %d/%d (%.0f%%)  <- directional hit-rate" % (won_dir, n, 100 * won_dir / n))
print("avg favorite entry price: %.3f (median %.3f)" % (statistics.mean(fav_entries), statistics.median(fav_entries)))
print("PnL split: pair $%+.0f | directional/naked $%+.0f (%.0f%% directional)"
      % (sum(r["pair_pnl"] for r in rows), sum(r["dir_pnl"] for r in rows),
         100 * sum(r["dir_pnl"] for r in rows) / tot if tot else 0))
print("5m PnL $%+.0f | 15m PnL $%+.0f" % (sum(r["pnl"] for r in tf5), sum(r["pnl"] for r in tf15)))

# ---- full fills for the top-6 PnL windows (what drove the gains) ----
print("\n--- FILLS of the TOP-6 windows by PnL (how he won them) ---")
for r in sorted(rows, key=lambda x: -x["pnl"])[:6]:
    print("\n  %s Kyiv %s (%s) win=%s spent=$%.0f PnL=$%+.0f  fav_entry %.3f" %
          (hh(r["ts"], KYIV), r["tf"], r["slug"], r["win"], r["spent"], r["pnl"], r["fav_avg"]))
    for ts, side, sz, px in sorted(r["fills"]):
        mark = "  <-WIN" if side == r["win"] else ""
        print("      %s  %-3s %5.0f @ %.3f%s" % (hh(ts, KYIV), side, sz, px, mark))
