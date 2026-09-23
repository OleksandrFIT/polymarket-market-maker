"""CAN WE assemble balanced pairs < $1, continuously, like the guru?

Simulate a continuous balanced-pair MAKER on the REAL 15m trade tape (every real
counterparty trade). The honest question: what AVG PAIR COST do we realize, and
is it < $1?

Model (top-of-book maker + complete-by-take, mirroring the guru's tape):
  - track best bid/ask per outcome from real trades (BUY print => ask, SELL => bid).
  - maker fill: when a real SELL on outcome X (size >= QUEUE_MIN) prints, our resting
    top-of-book bid on X gets hit -> we BUY FILL @ bid_X, IF it keeps us balanced
    (inv_X - inv_other < NAKED_CAP). This is the cheap leg.
  - complete-by-take: whenever naked > NAKED_CAP, buy the DEFICIT leg at its ask
    (pay up, like he chases the favorite). Keeps the book balanced -> pairs locked.
  - at resolution: PnL = winner_inv*$1 - total_cost. Report pair cost + naked drag.

This tells us if the guru's edge is physically reachable from the tape, BEFORE we
rebuild the ladder. Read-only. Caches 15m tapes under /tmp/poly_tape15_cache.
"""
import urllib.request, json, os, time, statistics

UA = {"User-Agent": "Mozilla/5.0"}
CACHE = "/tmp/poly_tape15_cache"
os.makedirs(CACHE, exist_ok=True)
STEP = 900
END_TS = 1781631000
N_WANT = 140

DELTA = 0.0            # maker improvement vs best bid (0 = just join best bid)
QUEUE_MIN = 5          # sell size needed to clear queue ahead of our 5
FILL = 5
NAKED_CAP = 10         # max |inv_up - inv_down| before we must complete
LO_T, HI_T = 0, 870    # act through the window (stop 30s before settle)


def _get(url, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.8)


def meta(ts):
    r = _get("https://gamma-api.polymarket.com/markets?slug=btc-updown-15m-%d&closed=true" % ts)
    if not isinstance(r, list) or not r:
        return None
    m = r[0]
    cid = m.get("conditionId")
    toks = m.get("clobTokenIds")
    if isinstance(toks, str):
        toks = json.loads(toks)
    if not cid or not toks or len(toks) < 2:
        return None
    return cid


def tape(ts):
    fn = os.path.join(CACHE, "%d.json" % ts)
    if os.path.exists(fn):
        return json.load(open(fn))
    cid = meta(ts)
    if not cid:
        json.dump(None, open(fn, "w")); return None
    all_tr, off = [], 0
    while True:
        tr = _get("https://data-api.polymarket.com/trades?market=%s&limit=500&offset=%d" % (cid, off))
        if not isinstance(tr, list) or not tr:
            break
        all_tr += tr
        off += len(tr)
        if len(tr) < 500:
            break
    win = [(int(t["timestamp"]) - ts, t["outcome"], t["side"], float(t["price"]), float(t["size"]))
           for t in all_tr if ts <= int(t["timestamp"]) < ts + STEP]
    win.sort()
    json.dump(win, open(fn, "w"))
    return win


def simulate(twin):
    """returns (pairs, pair_cost, inv_up, inv_down, cost_total, last_up_price)"""
    inv = {"Up": 0.0, "Down": 0.0}
    cost = 0.0
    ask = {"Up": 0.5, "Down": 0.5}
    bid = {"Up": 0.5, "Down": 0.5}
    last_up = 0.5
    base = twin[0][0] if twin else 0          # normalize: cache may hold absolute ts
    for (t, outc, side, px, sz) in twin:
        if outc not in ("Up", "Down"):
            continue
        trel = t - base
        other = "Down" if outc == "Up" else "Up"
        # update book proxy
        if side == "BUY":
            ask[outc] = px
        else:
            bid[outc] = px
        if outc == "Up":
            last_up = px
        else:
            last_up = 1 - px
        if not (LO_T <= trel <= HI_T):
            continue
        # (1) maker fill: a real SELL hits our top-of-book bid on `outc`
        if side == "SELL" and sz >= QUEUE_MIN:
            if inv[outc] - inv[other] < NAKED_CAP:        # only if it keeps us balanced
                fillpx = max(0.01, bid[outc] - DELTA)
                inv[outc] += FILL
                cost += FILL * fillpx
        # (2) complete-by-take: if naked beyond cap, buy the deficit leg at its ask
        naked = inv["Up"] - inv["Down"]
        if abs(naked) > NAKED_CAP:
            deficit = "Down" if naked > 0 else "Up"
            takepx = min(0.99, ask[deficit])
            inv[deficit] += FILL
            cost += FILL * takepx
    pairs = min(inv["Up"], inv["Down"])
    pair_cost = (cost - 0.0) / pairs if pairs > 0 else 0.0
    # pair_cost approx: total cost / pairs is muddied by excess; report cost/total_shares too
    return inv["Up"], inv["Down"], cost, last_up


print("fetching up to %d real 15m tapes (cached)..." % N_WANT)
rows = []
ts = END_TS; miss = 0
while len(rows) < N_WANT and miss < 60:
    try:
        tw = tape(ts)
    except Exception:
        tw = None
    if tw and len(tw) >= 20:
        rows.append((ts, tw)); miss = 0
    else:
        miss += 1
    ts -= STEP
    time.sleep(0.03)
print("got %d windows with real tapes\n" % len(rows))

tot_pnl = 0.0
pair_costs = []
win_pnls = []
lt1 = 0
for ts, tw in rows:
    up_q, dn_q, cost, last_up = simulate(tw)
    if up_q < FILL or dn_q < FILL:
        continue
    winner = "Up" if last_up >= 0.5 else "Down"
    payout = (up_q if winner == "Up" else dn_q) * 1.0
    pnl = payout - cost
    pairs = min(up_q, dn_q)
    paircost = cost / (up_q + dn_q) * 2          # avg $ per share *2 = per-pair proxy
    pair_costs.append(paircost)
    win_pnls.append(pnl)
    tot_pnl += pnl
    if paircost < 1.0:
        lt1 += 1

n = len(win_pnls)
print("windows simulated     : %d" % n)
print("avg pair cost (2*$/sh): $%.3f   (%d/%d windows < $1.00 = %.0f%%)"
      % (statistics.mean(pair_costs), lt1, n, 100*lt1/n))
print("total PnL             : $%+.2f   (avg $%+.3f/window)" % (tot_pnl, tot_pnl/n))
print("windows + / -         : %d / %d" % (sum(1 for p in win_pnls if p > 0), sum(1 for p in win_pnls if p <= 0)))
print("best / worst window   : $%+.2f / $%+.2f" % (max(win_pnls), min(win_pnls)))
print("\nNOTE: 5-share fills; scale linearly with size. pair cost < $1 => edge reachable.")
