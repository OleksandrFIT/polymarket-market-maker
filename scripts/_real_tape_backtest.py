"""REAL-DATA backtest: simulate the bot against the ACTUAL Polymarket trade tape
(every real trade, per-second) — so fills only happen if a real counterparty
traded at our price. This replaces the Binance+Phi PROXY that over-stated fills.

Pipeline:
  enumerate windows (gamma slug+closed=true -> tokens, conditionId, resolution)
  -> fetch full trade tape per window (data-api /trades, paginated, cached)
  -> simulate ladder+naked policy event-by-event on the tape
  -> validate vs our OWN realized PnL on windows we actually traded
  -> scale to many windows; report case breakdown + policy comparison.

Fill model (the one honest assumption = queue position):
  our resting BUY at price P (maker bid) fills 5 shares when a real SELL trade
  prints at price <= P with size >= QUEUE_MIN (someone sold into our level with
  enough size to clear the queue ahead of us). We pay OUR bid P.
  taker COMPLETE buys the light leg at the prevailing ASK (recent BUY-trade px).
  taker SELL dumps the heavy leg at the prevailing BID (recent SELL-trade px).

Read-only. Caches tapes under /tmp/poly_tape_cache.
"""
import urllib.request, json, os, time, statistics

UA = {"User-Agent": "Mozilla/5.0"}
CACHE = "/tmp/poly_tape_cache"
os.makedirs(CACHE, exist_ok=True)
RUNGS = [0.49, 0.46]
RUNG_SIZE = 5
NAKED_CAP = 5
POST_CAP = NAKED_CAP + RUNG_SIZE      # 10, lag-proof backstop
MIN_BUY = 0.42
QUEUE_MIN = 5                          # sell size needed to fill our 5 (queue proxy)


def _get(url, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception as e:
            if k == tries - 1:
                raise
            time.sleep(1.0)


def market_meta(ts):
    """gamma -> (conditionId, up_token, down_token) for btc-updown-5m-<ts>, or None."""
    slug = "btc-updown-5m-%d" % ts
    r = _get("https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % slug)
    if not isinstance(r, list) or not r:
        return None
    m = r[0]
    cid = m.get("conditionId")
    toks = m.get("clobTokenIds")
    if isinstance(toks, str):
        toks = json.loads(toks)
    if not cid or not toks or len(toks) < 2:
        return None
    return cid, toks[0], toks[1]   # [Up, Down] by gamma convention


def tape(ts, cid):
    """Full in-window trade tape, cached. -> sorted [(t, outcome, side, price, size)]."""
    fn = os.path.join(CACHE, "%d.json" % ts)
    if os.path.exists(fn):
        return json.load(open(fn))
    all_tr = []
    off = 0
    while True:
        tr = _get("https://data-api.polymarket.com/trades?market=%s&limit=500&offset=%d" % (cid, off))
        if not isinstance(tr, list) or not tr:
            break
        all_tr += tr
        off += len(tr)
        if len(tr) < 500:
            break
    win = [(int(t["timestamp"]), t["outcome"], t["side"], float(t["price"]), float(t["size"]))
           for t in all_tr if ts <= int(t["timestamp"]) < ts + 300]
    win.sort()
    json.dump(win, open(fn, "w"))
    return win


def winner_binance(ts):
    k = _get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&startTime=%d&limit=1" % (ts * 1000))[0]
    return "Up" if float(k[4]) >= float(k[1]) else "Down"


def simulate(ts, twin, winner, gate_sec=60, policy="gate"):
    """Event-driven sim over the real tape. Returns dict with PnL + case label."""
    inv = {"Up": 0, "Down": 0}
    cost = {"Up": 0.0, "Down": 0.0}
    rung = {"Up": 0, "Down": 0}        # index into RUNGS for the active bid
    posted = {"Up": 0, "Down": 0}
    last_px = {"Up": 0.5, "Down": 0.5}
    completed = sold = False
    # track recent ask/bid from the tape (BUY trade px ~ ask, SELL trade px ~ bid)
    for (t, out, side, px, sz) in twin:
        opp = "Down" if out == "Up" else "Up"
        last_px[out] = px
        tl = ts + 300 - t
        p_up = last_px["Up"] if last_px["Up"] else 0.5
        # detector bias proxy from the live market price (acts only late, like the bot)
        bias = "NEUTRAL"
        if tl <= 60:
            if p_up > 0.60:
                bias = "UP"
            elif p_up < 0.40:
                bias = "DOWN"
        # ---- gate: resolve naked near the end ----
        if policy != "ride" and tl <= gate_sec and inv["Up"] != inv["Down"] and not (completed or sold):
            if inv["Up"] > inv["Down"]:
                heavy, light = "Up", "Down"
            else:
                heavy, light = "Down", "Up"
            nq = inv[heavy] - inv[light]
            havg = cost[heavy] / inv[heavy] if inv[heavy] else 0.0
            light_ask = min(0.99, last_px[light] + 0.01)
            heavy_bid = max(0.01, last_px[heavy] - 0.01)
            if havg + light_ask < 1.0:                 # COMPLETE (taker buy light)
                inv[light] += nq; cost[light] += nq * light_ask; completed = True
            else:                                       # SELL heavy (taker)
                inv[heavy] -= nq; cost[heavy] -= nq * (cost[heavy] / inv[heavy] if inv[heavy] else 0)
                # proceeds tracked separately:
                sold = True; sold_proceeds = nq * heavy_bid
                cost[heavy] = max(0.0, cost[heavy]);
                # store proceeds on the object via closure dict
                _sold_px[0] = heavy_bid; _sold_qty[0] = nq
        # ---- maker fill: a real SELL at <= our active bid fills our rung ----
        if side == "SELL":
            bid = RUNGS[rung[out]] if rung[out] < len(RUNGS) else None
            if bid is not None and bid >= MIN_BUY and px <= bid and sz >= QUEUE_MIN:
                naked_if = (inv[out] + RUNG_SIZE) - inv[opp]
                suppressed = (bias == "UP" and out == "Down") or (bias == "DOWN" and out == "Up")
                if (abs(naked_if) <= POST_CAP and posted[out] + RUNG_SIZE <= POST_CAP
                        and not suppressed and not completed and not sold):
                    inv[out] += RUNG_SIZE; cost[out] += RUNG_SIZE * bid
                    posted[out] += RUNG_SIZE; rung[out] += 1
    payout = inv[winner] * 1.0
    proceeds = _sold_qty[0] * _sold_px[0] if sold else 0.0
    buy_total = cost["Up"] + cost["Down"] if not sold else None
    # recompute buy_total cleanly from fills (cost may be dented by the sell math)
    return payout, proceeds, inv, sold, completed


# shared scratch for the sell (avoids restructuring the loop return)
_sold_px = [0.0]; _sold_qty = [0.0]


def run_window(ts, gate_sec=60, policy="gate"):
    meta = market_meta(ts)
    if not meta:
        return None
    cid, up_tok, dn_tok = meta
    twin = tape(ts, cid)
    if not twin:
        return None
    winner = winner_binance(ts)
    # reset scratch
    _sold_px[0] = 0.0; _sold_qty[0] = 0.0
    # we need clean buy accounting -> re-run with explicit tracking
    return _sim_clean(ts, twin, winner, gate_sec, policy)


def _sim_clean(ts, twin, winner, gate_sec, policy):
    inv = {"Up": 0, "Down": 0}; cost = {"Up": 0.0, "Down": 0.0}
    rung = {"Up": 0, "Down": 0}; posted = {"Up": 0, "Down": 0}
    last_px = {"Up": 0.5, "Down": 0.5}
    buy = 0.0; proceeds = 0.0; acted = False; action = "ride"
    for (t, out, side, px, sz) in twin:
        opp = "Down" if out == "Up" else "Up"
        last_px[out] = px
        tl = ts + 300 - t
        p_up = last_px["Up"]
        bias = "NEUTRAL"
        if tl <= 60:
            bias = "UP" if p_up > 0.60 else ("DOWN" if p_up < 0.40 else "NEUTRAL")
        if policy != "ride" and tl <= gate_sec and inv["Up"] != inv["Down"] and not acted:
            heavy, light = ("Up", "Down") if inv["Up"] > inv["Down"] else ("Down", "Up")
            nq = inv[heavy] - inv[light]
            havg = cost[heavy] / inv[heavy] if inv[heavy] else 0.0
            light_ask = min(0.99, last_px[light] + 0.01)
            heavy_bid = max(0.01, last_px[heavy] - 0.01)
            if havg + light_ask < 1.0:
                buy += nq * light_ask; inv[light] += nq; acted = True; action = "complete"
            else:
                proceeds += nq * heavy_bid; inv[heavy] -= nq; acted = True; action = "sell"
        if side == "SELL":
            bid = RUNGS[rung[out]] if rung[out] < len(RUNGS) else None
            if bid is not None and bid >= MIN_BUY and px <= bid and sz >= QUEUE_MIN:
                naked_if = (inv[out] + RUNG_SIZE) - inv[opp]
                suppressed = (bias == "UP" and out == "Down") or (bias == "DOWN" and out == "Up")
                if abs(naked_if) <= POST_CAP and posted[out] + RUNG_SIZE <= POST_CAP and not suppressed and not acted:
                    inv[out] += RUNG_SIZE; cost[out] += RUNG_SIZE * bid
                    buy += RUNG_SIZE * bid; posted[out] += RUNG_SIZE; rung[out] += 1
    payout = inv[winner] * 1.0
    pnl = payout + proceeds - buy
    pairs = min(inv["Up"], inv["Down"]); naked = abs(inv["Up"] - inv["Down"])
    if inv["Up"] == 0 and inv["Down"] == 0:
        case = "sit"
    elif naked == 0:
        case = "clean_pair"
    else:
        case = action  # ride / complete / sell
    return {"ts": ts, "pnl": pnl, "pairs": pairs, "naked": naked, "case": case,
            "up": inv["Up"], "dn": inv["Down"], "winner": winner, "buy": buy}


if __name__ == "__main__":
    import sys
    # VALIDATION mode: run on the windows we actually traded today, compare to known PnL
    traded = [1781459100, 1781459400, 1781459700, 1781460000, 1781460300,
              1781460600, 1781460900, 1781461200, 1781461500, 1781461800]
    print("=== VALIDATION: sim vs our REAL traded windows (policy=ride to match live) ===")
    for ts in traded:
        try:
            r = run_window(ts, policy="ride")
            if r:
                print("w%d sim: Up=%d Dn=%d naked=%d %s payout-based PnL=$%+.2f (case %s)" %
                      (ts % 100000, r["up"], r["dn"], r["naked"], r["winner"], r["pnl"], r["case"]))
        except Exception as e:
            print("w%d err %s" % (ts % 100000, e))
