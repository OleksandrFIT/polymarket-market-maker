"""24h FORENSIC of wallet 0xb945...db68 (on-chain name l5Zn1bWoM8eTsK).

Reconstructs every 15m BTC window traded in the last 24h and decomposes realized
PnL into:
  - PAIR edge  = min(up,down) shares matched, risk-free (1 - (up_avg+down_avg))
                 -> market-making / merge / spread capture
  - NAKED edge = excess shares on the heavy side, win/lose by resolution
                 -> directional / momentum / favorite tilt

Winner is taken from REDEEM activity (definitive payout) when present, else from
the UP-token's clob price at settle. Held-to-resolution model (wallet never sells).
Read-only.
"""
import urllib.request, json, time, collections, statistics, datetime as dt

WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
BASE = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0"}
NOW = int(time.time())
SINCE = NOW - 24*3600


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))


def page(endpoint, limit=500):
    out, off = [], 0
    while True:
        try:
            b = get("%s/%s?user=%s&limit=%d&offset=%d" % (BASE, endpoint, WALLET, limit, off))
        except Exception:
            break
        if not b:
            break
        keep = [x for x in b if x.get("timestamp", 0) >= SINCE]
        out += keep
        if len(keep) < len(b) or len(b) < limit:
            break
        off += limit
        time.sleep(0.12)
    return out


trades = page("trades")
acts = page("activity", limit=100)
redeems = collections.defaultdict(float)   # conditionId -> usdc redeemed
for a in acts:
    if a.get("type", "").upper() == "REDEEM":
        redeems[a["conditionId"]] += float(a.get("usdcSize", 0))

# ---- per-window aggregation ----
W = collections.defaultdict(lambda: {"slug": "", "title": "", "ts": 0, "up_tok": None,
                                     "up_sh": 0.0, "up_usd": 0.0, "dn_sh": 0.0, "dn_usd": 0.0,
                                     "first": 0, "last": 0, "n": 0})
for t in trades:
    if t.get("side") != "BUY":
        continue
    c = t["conditionId"]
    w = W[c]
    w["slug"] = t.get("slug", "")
    w["title"] = t.get("title", "")
    try:
        w["ts"] = int(t["slug"].split("-")[-1])
    except Exception:
        pass
    sz = float(t["size"]); px = float(t["price"]); usd = sz*px
    ts = t.get("timestamp", 0)
    w["n"] += 1
    w["first"] = min(w["first"] or ts, ts); w["last"] = max(w["last"], ts)
    if t.get("outcomeIndex") == 0:          # Up
        w["up_sh"] += sz; w["up_usd"] += usd
        if not w["up_tok"]: w["up_tok"] = t.get("asset")
    else:                                   # Down
        w["dn_sh"] += sz; w["dn_usd"] += usd


def resolve_up(tok, ts):
    """winner via UP-token clob price at settle: 'Up' if last>=0.5 else 'Down'."""
    if not tok or not ts:
        return None
    try:
        r = get("https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=1"
                % (tok, ts, ts + 1000))
        h = r.get("history", []) if isinstance(r, dict) else []
        if not h:
            return None
        return "Up" if h[-1]["p"] >= 0.5 else "Down"
    except Exception:
        return None


rows = []
for c, w in W.items():
    spent = w["up_usd"] + w["dn_usd"]
    pairs = min(w["up_sh"], w["dn_sh"])
    up_avg = w["up_usd"]/w["up_sh"] if w["up_sh"] else 0
    dn_avg = w["dn_usd"]/w["dn_sh"] if w["dn_sh"] else 0
    pair_cost = up_avg + dn_avg
    heavy = "Up" if w["up_sh"] >= w["dn_sh"] else "Down"
    naked_sh = abs(w["up_sh"] - w["dn_sh"])
    heavy_avg = up_avg if heavy == "Up" else dn_avg
    # winner
    winner = None
    if c in redeems:
        winner = "redeem"      # definitive payout known
    ended = w["ts"] and (w["ts"] + 900 + 60) < NOW
    up_win = resolve_up(w["up_tok"], w["ts"] + 900) if ended else None
    rows.append({"c": c, "ts": w["ts"], "title": w["title"], "n": w["n"],
                 "up_sh": w["up_sh"], "dn_sh": w["dn_sh"], "up_avg": up_avg, "dn_avg": dn_avg,
                 "spent": spent, "pairs": pairs, "pair_cost": pair_cost, "naked_sh": naked_sh,
                 "heavy": heavy, "heavy_avg": heavy_avg, "redeem": redeems.get(c, 0.0),
                 "up_win": up_win, "ended": ended})

rows.sort(key=lambda r: r["ts"])

# ---- PnL decomposition ----
tot_spent = tot_pair_pnl = tot_naked_pnl = tot_realized = 0.0
n_res = n_open = 0
pair_costs = []
naked_on_winner = naked_on_loser = 0
big = []
for r in rows:
    if not r["ended"] and r["redeem"] == 0:
        n_open += 1
        continue
    n_res += 1
    # winner side
    if r["up_win"] in ("Up", "Down"):
        win = r["up_win"]
    elif r["redeem"] > 0:
        # infer: redeem ~= winning shares; pick side whose shares ~matches redeem
        win = "Up" if abs(r["up_sh"] - r["redeem"]) <= abs(r["dn_sh"] - r["redeem"]) else "Down"
    else:
        continue
    win_sh = r["up_sh"] if win == "Up" else r["dn_sh"]
    pair_pnl = r["pairs"] * (1.0 - r["pair_cost"])
    if r["pairs"] > 0:
        pair_costs.append(r["pair_cost"])
    # naked: excess on heavy side
    if r["heavy"] == win:
        naked_pnl = r["naked_sh"] * (1.0 - r["heavy_avg"]); naked_on_winner += 1
    else:
        naked_pnl = -r["naked_sh"] * r["heavy_avg"]; naked_on_loser += 1
    realized = win_sh * 1.0 - r["spent"]
    tot_spent += r["spent"]; tot_pair_pnl += pair_pnl; tot_naked_pnl += naked_pnl
    tot_realized += realized
    big.append((realized, r, win, pair_pnl, naked_pnl))

print("="*72)
print("  24h FORENSIC — wallet 0xb945...db68 (l5Zn1bWoM8eTsK)")
print("  window: %s .. %s UTC" % (dt.datetime.utcfromtimestamp(SINCE).strftime("%m-%d %H:%M"),
                                  dt.datetime.utcfromtimestamp(NOW).strftime("%m-%d %H:%M")))
print("="*72)
print("trades(BUY): %d | windows: %d | resolved: %d | still-open: %d" %
      (len([t for t in trades if t.get('side')=='BUY']), len(rows), n_res, n_open))
print("all sells: %d  (never-sell strategy)" % len([t for t in trades if t.get('side')=='SELL']))
print()
print("REALIZED PnL (resolved windows): $%+.0f  on $%.0f spent  (%.1f%% of spend)" %
      (tot_realized, tot_spent, 100*tot_realized/tot_spent if tot_spent else 0))
print("  reconstructed check (pair+naked): $%+.0f" % (tot_pair_pnl + tot_naked_pnl))
print()
print("--- PROFIT STRUCTURE ---")
tp = tot_pair_pnl + tot_naked_pnl
print("  PAIR edge   (matched, risk-free): $%+8.0f   %5.1f%%" %
      (tot_pair_pnl, 100*tot_pair_pnl/tp if tp else 0))
print("  NAKED edge  (directional/tilt) :  $%+8.0f   %5.1f%%" %
      (tot_naked_pnl, 100*tot_naked_pnl/tp if tp else 0))
print()
if pair_costs:
    print("  matched-pair avg cost: $%.3f (median $%.3f)  -> edge %.1f c/pair" %
          (statistics.mean(pair_costs), statistics.median(pair_costs),
           100*(1-statistics.mean(pair_costs))))
print("  naked heavy side landed on WINNER: %d windows | on LOSER: %d windows" %
      (naked_on_winner, naked_on_loser))
print()
print("--- TOP 8 WINDOWS BY REALIZED PnL ---")
print(" time(UTC)        spent   pairs  paircost  nakedSh heavy  win   pairPnL nakedPnL  realized")
for realized, r, win, pp, np_ in sorted(big, key=lambda x: -x[0])[:8]:
    print(" %s  %6.0f  %6.0f  %.3f   %6.0f  %-4s  %-4s  %+6.0f  %+6.0f   %+7.0f" %
          (dt.datetime.utcfromtimestamp(r["ts"]).strftime("%m-%d %H:%M"), r["spent"], r["pairs"],
           r["pair_cost"], r["naked_sh"], r["heavy"], win, pp, np_, realized))
print()
print("--- ENTRY PRICE DISTRIBUTION (all BUY fills, 24h) ---")
buckets = collections.defaultdict(lambda: [0, 0.0])
for t in trades:
    if t.get("side") != "BUY": continue
    p = float(t["price"]); b = int(p*10)/10.0
    buckets[b][0] += 1; buckets[b][1] += float(t["size"])
print(" price-bucket   n_fills   shares")
for b in sorted(buckets):
    n, sh = buckets[b]
    print("  %.1f-%.1f      %6d   %8.0f" % (b, b+0.1, n, sh))
