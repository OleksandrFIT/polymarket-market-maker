"""Calibrate the regime_max_move_usd threshold + show ALL naked-handling cases.

For each resolved 5m window we (a) compute the ENTRY signal the live gate uses — BTC's net
$ move over the trailing 5 min at window open (Binance 1m klines), and (b) SIMULATE the full
top_book strategy on the window's REAL taker tape: bid best-1tick both sides, fill on crossing
SELL takers, skew-cap 6, committed-cap $15, merge, and near-end COMPLETE (buy light if pair
<$1, self-funding $6 budget) / SELL (sell heavy loser if pair >=$1). Then bucket windows by
|entry signal| and report per-bucket PnL + case mix (clean / completed / sold / naked-rode).

Finds the move-size where window PnL crosses zero -> the calibrated gate threshold: below it
the pair edge wins, above it the flow is too one-sided. Book is modelled as last-trade +/- a
half-spread (HALFSPREAD) — absolute PnL is approximate; the SHAPE (PnL vs move) + case mix are
the deliverable. Run on the server (tape cache). Usage: python3 scripts/_regime_calibrate.py [N]
"""
import sys
import json
import urllib.request
import collections

from quoter.research.mm_tape import load_window

N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
SIZE, NAKED_CAP, PWC, CBUD, TICK = 5.0, 6.0, 15.0, 6.0, 0.001
TB_MERGE_MIN, GATE, WIN = 5.0, 45.0, 300
HALFSPREAD = float(sys.argv[2]) if len(sys.argv) > 2 else 0.010
MOM_THRESH = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0   # BTC move ($) to fire momentum
MOM_LOOKBACK = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0   # trend window (sec); 0 = since open
MOM_FAV, MOM_BUDGET, MOM_DECISION = 0.60, 15.0, 120              # favorite cutoff, $/win, sec in
OI = {"Up": 0, "Down": 1}
NOW = int(__import__("time").time())
BASE = (NOW // 300) * 300 - 600            # newest fully-resolved 5m window


def _get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20))


def btc_1m(start_ms, end_ms):
    """minute_sec -> close, over [start,end] (Binance, paged by 1000)."""
    out = {}
    t = start_ms
    while t < end_ms:
        k = _get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m"
                 "&startTime=%d&endTime=%d&limit=1000" % (t, min(end_ms, t + 1000 * 60000)))
        if not k:
            break
        for c in k:
            out[int(c[0]) // 1000] = float(c[4])
        t = int(k[-1][0]) + 60000
    return out


def entry_signal(btc, open_ts):
    """BTC net $ move over the trailing 5 min at window open (the live gate's input)."""
    a = btc.get((open_ts - 300) // 60 * 60)
    b = btc.get(open_ts // 60 * 60)
    return (b - a) if (a and b) else None


def simulate2(tape, winner, open_ts):
    """Share-accounting version -> (pnl, case)."""
    end_ts = open_ts + WIN
    last = {0: 0.5, 1: 0.5}
    inv = {"Up": 0.0, "Down": 0.0}      # currently-held shares
    cost = {"Up": 0.0, "Down": 0.0}     # gross $ spent (never decremented)
    held = {"Up": 0.0, "Down": 0.0}     # $ of held shares
    completed = {"Up": 0.0, "Down": 0.0}
    ret = 0.0                            # $ returned by merges + sells
    pair_ret = pair_cost = 0.0           # decompose: PnL from PAIRS vs from NAKED legs
    bytick = collections.defaultdict(list)
    for x in tape:
        if open_ts <= x["ts"] < end_ts:
            bytick[int((x["ts"] - open_ts) // 2)].append(x)
    did_complete = did_sell = False

    for k in range(WIN // 2):
        trem = end_ts - (open_ts + k * 2)
        our = {s: round(last[OI[s]] - HALFSPREAD + TICK, 3) for s in ("Up", "Down")}
        quote = {}
        for side in ("Up", "Down"):
            other = "Down" if side == "Up" else "Up"
            if inv[side] + SIZE - inv[other] > NAKED_CAP:
                continue
            if cost["Up"] + cost["Down"] + our[side] * SIZE > PWC:
                continue
            if not (0 < our[side] < 0.99):
                continue
            quote[side] = our[side]
        if len(quote) == 2 and quote["Up"] + quote["Down"] > 0.999:
            del quote[max(quote, key=lambda s: quote[s])]
        vol = {"Up": 0.0, "Down": 0.0}
        for x in bytick.get(k, []):
            last[x["oi"]] = x["price"]
            side = "Up" if x["oi"] == 0 else "Down"
            if x["side"] == "SELL" and side in quote and x["price"] <= quote[side]:
                vol[side] += x["size"]
        for side in ("Up", "Down"):
            if side in quote and vol[side] > 0:
                f = min(SIZE, vol[side])
                inv[side] += f; cost[side] += f * quote[side]; held[side] += f * quote[side]
        mmin = 1.0 if trem <= GATE else TB_MERGE_MIN
        m = min(inv["Up"], inv["Down"])
        if m >= mmin and m > 0:
            pc = 0.0
            for s in ("Up", "Down"):
                avg = held[s] / inv[s] if inv[s] > 0 else 0.0
                pc += m * avg
                held[s] = max(0.0, held[s] - m * avg); inv[s] -= m
            ret += m * 1.0; pair_ret += m * 1.0; pair_cost += pc   # pair returns $1, cost pc
        if trem <= GATE:
            naked = inv["Up"] - inv["Down"]
            if abs(naked) >= 1:
                light = "Down" if naked > 0 else "Up"
                heavy = "Up" if naked > 0 else "Down"
                havg = held[heavy] / inv[heavy] if inv[heavy] > 0 else None
                lask = last[OI[light]] + HALFSPREAD
                hbid = max(0.0, last[OI[heavy]] - HALFSPREAD)
                bl = (PWC + CBUD) - (cost["Up"] + cost["Down"])
                q = max(0.0, abs(naked) - completed[light])
                if lask > 0:
                    q = float(int(min(q, bl / lask)))
                if q >= 1 and havg is not None and (havg + lask) < 1.0:
                    inv[light] += q; cost[light] += q * lask; held[light] += q * lask
                    completed[light] += q; did_complete = True
                    mm = min(inv["Up"], inv["Down"])
                    if mm > 0:
                        pc = 0.0
                        for s in ("Up", "Down"):
                            avg = held[s] / inv[s] if inv[s] > 0 else 0.0
                            pc += mm * avg
                            held[s] = max(0.0, held[s] - mm * avg); inv[s] -= mm
                        ret += mm * 1.0; pair_ret += mm * 1.0; pair_cost += pc
                elif hbid > 0:
                    q2 = float(int(abs(naked)))
                    inv[heavy] -= q2
                    if inv[heavy] <= 0:
                        inv[heavy] = 0.0
                    ret += q2 * hbid; did_sell = True
    ret += inv[winner] * 1.0                                  # leftover held winner resolves $1
    pnl = ret - (cost["Up"] + cost["Down"])
    pair_pnl = pair_ret - pair_cost          # profit from all merged pairs
    naked_pnl = pnl - pair_pnl               # everything else = the naked legs
    naked_left = int(abs(inv["Up"] - inv["Down"]))
    case = ("sell" if did_sell else "complete" if did_complete
            else "naked_ride" if naked_left else "clean")
    return pnl, case, naked_left, pair_pnl, naked_pnl


def momentum(tape, winner, open_ts, btc):
    """LATE-WINDOW lag-harvest (reverse-engineered from 0xb27b's real trades): in the last
    60s, TAKER-buy the LEADING side when its price is in the edge zone [0.52,0.70] — the
    market lags the near-resolved outcome. Includes taker fee 0.07*p*(1-p). -> (pnl, fired)."""
    dts = open_ts + 240                      # last 60s of the 5m window
    up_p = dn_p = 0.5
    for x in tape:
        if x["ts"] > dts:
            break
        if x["oi"] == 0:
            up_p = x["price"]
        else:
            dn_p = x["price"]
    side, p = ("Up", up_p) if up_p >= dn_p else ("Down", dn_p)
    if not (0.52 <= p <= 0.70):              # edge zone: skip coinflips, dogs, done favorites
        return 0.0, False
    # BTC-momentum CONFIRMATION over the CURRENT trend (last MOM_LOOKBACK sec, not stale
    # since-open) so a mid-window reversal is caught: only buy if BTC is STILL moving in the
    # leader's direction right now. MOM_LOOKBACK<=0 -> since window open.
    trend_from = (dts - MOM_LOOKBACK) if MOM_LOOKBACK > 0 else open_ts
    b0, bd = btc.get(trend_from // 60 * 60), btc.get(dts // 60 * 60)
    if b0 is None or bd is None:
        return 0.0, False
    move = bd - b0
    if (side == "Up" and move < MOM_THRESH) or (side == "Down" and move > -MOM_THRESH):
        return 0.0, False
    ask = min(0.99, p + HALFSPREAD)
    fee = 0.07 * ask * (1 - ask)
    shares = int(MOM_BUDGET / ask)
    if shares < 1:
        return 0.0, False
    return shares * ((1.0 if winner == side else 0.0) - ask - fee), True


def main():
    slugs = [(BASE - k * 300) for k in range(N)]
    btc = btc_1m((min(slugs) - 400) * 1000, (max(slugs) + 60) * 1000)
    order = ["0-15", "15-30", "30-50", "50-80", "80-120", "120+"]

    def bkt(a):
        return ("0-15" if a < 15 else "15-30" if a < 30 else "30-50" if a < 50
                else "50-80" if a < 80 else "80-120" if a < 120 else "120+")

    B = collections.defaultdict(lambda: {"mk": [], "mo": [], "cb": [], "fired": 0})
    mom_fired = []
    for ots in slugs:
        w = load_window("btc-updown-5m-%d" % ots)
        if not w or not w[0] or not w[1]:
            continue
        tape, winner, _ = w
        sig = entry_signal(btc, ots)
        if sig is None:
            continue
        mk, _, _, pair_pnl, naked_pnl = simulate2(tape, winner, ots)
        mo, fired = momentum(tape, winner, ots, btc)
        b = bkt(abs(sig))
        B[b]["mk"].append(mk); B[b]["mo"].append(mo); B[b]["cb"].append(mk + mo)
        B[b].setdefault("pp", []).append(pair_pnl); B[b].setdefault("np", []).append(naked_pnl)
        if fired:
            B[b]["fired"] += 1; mom_fired.append(mo)

    used = sum(len(B[b]["mk"]) for b in B)
    print("windows: %d | HALFSPREAD=%.3f (~%.1f¢/pair) | momentum: BTC>$%d, favorite>%.2f, $%d/win\n"
          % (used, HALFSPREAD, HALFSPREAD * 200, MOM_THRESH, MOM_FAV, MOM_BUDGET))
    print("BTC 5m move │  n  │ MAKER  │ MOMENT │ COMBINED │ mom-fires")
    print("─" * 66)
    tot = {"mk": [], "mo": [], "cb": [], "fired": 0}
    for b in order:
        d = B.get(b)
        if not d or not d["mk"]:
            continue
        n = len(d["mk"])
        print("$%-11s │ %3d │ %+6.2f │ %+6.2f │ %+7.2f │ %d/%d (%.0f%%)"
              % (b, n, sum(d["mk"]) / n, sum(d["mo"]) / n, sum(d["cb"]) / n,
                 d["fired"], n, 100 * d["fired"] / n))
        for k in ("mk", "mo", "cb"):
            tot[k] += d[k]
        tot["fired"] += d["fired"]
    n = len(tot["mk"])
    print("─" * 66)
    print("%-13s│ %3d │ %+6.2f │ %+6.2f │ %+7.2f │ %d/%d (%.0f%%)"
          % ("ALL", n, sum(tot["mk"]) / n, sum(tot["mo"]) / n, sum(tot["cb"]) / n,
             tot["fired"], n, 100 * tot["fired"] / n))
    print("\nTOTALS over %d windows:  maker %+.1f  |  momentum %+.1f  |  combined %+.1f"
          % (n, sum(tot["mk"]), sum(tot["mo"]), sum(tot["cb"])))
    pp = [x for b in B.values() for x in b.get("pp", [])]
    np_ = [x for b in B.values() for x in b.get("np", [])]
    print("MAKER DECOMPOSITION:  from PAIRS %+.1f (%+.3f/win)  |  from NAKED legs %+.1f (%+.3f/win)"
          % (sum(pp), sum(pp) / len(pp), sum(np_), sum(np_) / len(np_)))
    if mom_fired:
        print("momentum ON THE %d WINDOWS IT FIRED: avg %+.2f/win, total %+.1f"
              % (len(mom_fired), sum(mom_fired) / len(mom_fired), sum(mom_fired)))


if __name__ == "__main__":
    main()
