"""Why does the wallet ALSO buy the cheap OPPOSITE (eventual-loser) side?
Hypothesis: insurance against a sudden trend reversal.

24h forensic of 0xb945...db68. Per window: resolve winner, split his position into
the HEAVY (directional bet) and LIGHT (opposite/cheap) side, and measure what the
light side costs and pays. Then split windows into:
  - RIGHT  (heavy side won)  -> light side = insurance premium that expired worthless
  - REVERSAL (heavy lost)    -> light side WON -> insurance payout cushioning the loss
Quantifies whether the opposite-leg buying is a net hedge (pays on reversals) or a
drag accepted to cut variance. Read-only.
"""
import urllib.request, json, time, collections, statistics, datetime as dt

WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
BASE = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0"}
NOW = int(time.time())
SINCE = NOW - 24*3600


def get(url, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.5)


def page(endpoint, limit=500):
    out, off = [], 0
    while True:
        b = get("%s/%s?user=%s&limit=%d&offset=%d" % (BASE, endpoint, WALLET, limit, off))
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
redeems = collections.defaultdict(float)
for a in (acts or []):
    if a.get("type", "").upper() == "REDEEM":
        redeems[a["conditionId"]] += float(a.get("usdcSize", 0))

W = collections.defaultdict(lambda: {"ts": 0, "up_tok": None, "up_sh": 0.0, "up_usd": 0.0,
                                     "dn_sh": 0.0, "dn_usd": 0.0,
                                     "up_fills": [], "dn_fills": []})
for t in trades:
    if t.get("side") != "BUY":
        continue
    w = W[t["conditionId"]]
    try:
        w["ts"] = int(t["slug"].split("-")[-1])
    except Exception:
        pass
    sz, px = float(t["size"]), float(t["price"])
    if t.get("outcomeIndex") == 0:
        w["up_sh"] += sz; w["up_usd"] += sz*px; w["up_fills"].append((px, sz))
        if not w["up_tok"]: w["up_tok"] = t.get("asset")
    else:
        w["dn_sh"] += sz; w["dn_usd"] += sz*px; w["dn_fills"].append((px, sz))


def resolve(tok, ts):
    if not tok or not ts:
        return None
    r = get("https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=1"
            % (tok, ts + 900, ts + 1000))
    h = r.get("history", []) if isinstance(r, dict) else []
    if not h:
        return None
    return "Up" if h[-1]["p"] >= 0.5 else "Down"


# per-window hedge breakdown
right = []      # heavy side won
reversal = []   # heavy side lost (light = winner)
loser_fills = []   # (price, shares) on the eventual-loser side, across all windows
winner_fills = []
tot_loser_usd = tot_winner_usd = 0.0
for c, w in W.items():
    if not (w["ts"] and (w["ts"]+960) < NOW):
        continue
    win = resolve(w["up_tok"], w["ts"])
    if win is None:
        continue
    heavy = "Up" if w["up_sh"] >= w["dn_sh"] else "Down"
    light = "Down" if heavy == "Up" else "Up"
    light_sh = w["dn_sh"] if light == "Down" else w["up_sh"]
    light_usd = w["dn_usd"] if light == "Down" else w["up_usd"]
    heavy_sh = w["up_sh"] if heavy == "Up" else w["dn_sh"]
    heavy_usd = w["up_usd"] if heavy == "Up" else w["dn_usd"]
    spent = w["up_usd"] + w["dn_usd"]
    win_sh = w["up_sh"] if win == "Up" else w["dn_sh"]
    realized = win_sh - spent
    # loser/winner fills
    lfills = w["dn_fills"] if win == "Up" else w["up_fills"]   # losing side fills
    wfills = w["up_fills"] if win == "Up" else w["dn_fills"]
    loser_fills += lfills; winner_fills += wfills
    los_usd = sum(p*s for p, s in lfills); tot_loser_usd += los_usd
    tot_winner_usd += sum(p*s for p, s in wfills)
    rec = {"ts": w["ts"], "spent": spent, "light_sh": light_sh, "light_usd": light_usd,
           "light_avg": light_usd/light_sh if light_sh else 0,
           "heavy_usd": heavy_usd, "realized": realized,
           "light_payout": (light_sh if light == win else 0.0)}
    if heavy == win:
        right.append(rec)
    else:
        reversal.append(rec)

n = len(right) + len(reversal)
print("="*70)
print("  OPPOSITE-SIDE (cheap hedge) ANALYSIS — 24h, wallet 0xb945...db68")
print("="*70)
print("resolved windows: %d  | heavy=winner (right): %d | heavy=loser (reversal): %d"
      % (n, len(right), len(reversal)))
print("directional hit-rate: %.0f%%\n" % (100*len(right)/n if n else 0))

print("--- how much he spends on the eventual-LOSER side (money to $0) ---")
print("  total spent on loser side : $%.0f  (%.0f%% of all spend)"
      % (tot_loser_usd, 100*tot_loser_usd/(tot_loser_usd+tot_winner_usd)))
print("  total spent on winner side: $%.0f" % tot_winner_usd)
if loser_fills:
    lp = [p for p, s in loser_fills]
    print("  loser-side fill price: avg $%.3f  median $%.3f  (he buys the loser CHEAP)"
          % (statistics.mean(lp), statistics.median(lp)))
print()

print("--- INSURANCE TEST: what the LIGHT (opposite) side does ---")
prem = sum(r["light_usd"] for r in right)          # paid on light side in right windows
prem_lost = prem                                    # expired worthless (light lost there)
payout = sum(r["light_payout"] for r in reversal)   # $1 each, light won the reversal
spent_light_rev = sum(r["light_usd"] for r in reversal)
print("  RIGHT windows (%d): light side cost $%.0f, expired ~worthless (premium)"
      % (len(right), prem_lost))
print("  REVERSAL windows (%d): light side PAID OUT $%.0f (cost was $%.0f) -> net +$%.0f here"
      % (len(reversal), payout, spent_light_rev, payout - spent_light_rev))
print()

print("--- variance effect on REVERSAL windows (where the bet was wrong) ---")
if reversal:
    avg_real_rev = statistics.mean(r["realized"] for r in reversal)
    avg_noheage = statistics.mean(-r["spent"] for r in reversal)  # if he'd held NO opposite, full loss
    worst_with = min(r["realized"] for r in reversal)
    print("  avg realized in reversal windows WITH opposite leg : $%+.1f/window" % avg_real_rev)
    print("  avg loss if he had NO opposite leg (full -spent)   : $%+.1f/window" % avg_noheage)
    print("  -> opposite leg cushions reversals by ~$%.1f/window" % (avg_real_rev - avg_noheage))
    print("  worst reversal window WITH leg: $%+.0f" % worst_with)
print()

# net contribution of the opposite-leg buying overall
net_light = payout - prem_lost - spent_light_rev + 0  # crude: payout minus all light spend
all_light_usd = sum(r["light_usd"] for r in right + reversal)
all_light_payout = payout
print("--- NET economics of buying the opposite side (all windows) ---")
print("  total spent on opposite/light side : $%.0f" % all_light_usd)
print("  total it paid back (reversals)     : $%.0f" % all_light_payout)
print("  NET opposite-leg PnL (excl. pair-matching): $%+.0f" % (all_light_payout - all_light_usd))
print("  => if negative: it's INSURANCE (─EV premium that caps reversal losses & cuts variance)")
print("     if positive: it's an edge on its own")
