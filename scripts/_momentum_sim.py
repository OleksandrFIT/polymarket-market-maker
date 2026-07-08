"""READ-ONLY live simulation of the momentum-take DECISION logic on the real BTC 5m market.
Polls live books every 2s, runs the SAME chase_signal + take decisions as `_momentum_window`,
and LOGS what it WOULD do (which side it chases, mover/fader asks, pair cost incl. taker fee,
simulated inventory + capped residual + merges) — but places NO orders. Lets us watch the
strategy make sane decisions on live data before risking money. GET-only.
Usage: python3 scripts/_momentum_sim.py [minutes]"""
import sys
import json
import time
import urllib.request

from quoter.research.chase import chase_signal
from quoter.runner.top_book_planner import taker_fee

WINDOW_SEC = 300
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (mom-sim)"}
LOOKBACK, THRESHOLD, CHASE_MAX, SIZE, RESID_CAP, PWC = 45.0, 0.06, 0.90, 5.0, 8.0, 20.0
MOVER_MIN, FADER_MAX = 0.55, 0.15   # his real economics: buy the mover at MODERATE prices
#   (avg ~0.65, mostly <0.70) + buy the fader ONLY when DEEP cheap (avg ~0.10) -> pair << $1.
#   The edge is TEMPORAL (cheap loser late), not simultaneous ask-pairing.
RUN_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0


def _get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10))


def _tokens(slug):
    m = _get("%s/markets?slug=%s" % (GAMMA, slug))
    if not m:
        return None
    toks = json.loads(m[0].get("clobTokenIds", "[]"))
    return (toks[0], toks[1]) if len(toks) == 2 else None


def _best(tid, side):
    try:
        b = _get("%s/book?token_id=%s" % (CLOB, tid))
    except Exception:
        return None
    lv = b.get(side) or []
    if not lv:
        return None
    ps = [float(x["price"]) for x in lv]
    return max(ps) if side == "bids" else min(ps)


def main():
    end = time.time() + RUN_MIN * 60
    cur_slug = None
    inv = {"Up": 0.0, "Down": 0.0}
    cost = 0.0
    merged = 0.0
    mid_hist = []
    committed = None
    toks = None
    while time.time() < end:
        now = time.time()
        slug = "btc-updown-5m-%d" % (int(now) // WINDOW_SEC * WINDOW_SEC)
        if slug != cur_slug:
            if cur_slug and mid_hist:
                win = "Up" if mid_hist[-1][1] >= 0.5 else "Down"
                net = inv["Up"] - inv["Down"]
                resid_side = "Up" if net > 0 else ("Down" if net < 0 else "flat")
                resid_pay = abs(net) if resid_side == win else 0.0     # winner residual redeems $1
                pnl = merged * 1.0 + resid_pay - cost
                print("  [%s done] winner=%s | merged=%.0f resid=%s%.0f (%s) | cost=$%.2f  SIM-PnL=$%+.2f" % (
                    cur_slug[-10:], win, merged, resid_side, abs(net),
                    "WON" if resid_side == win else ("LOST" if resid_side != "flat" else "-"), cost, pnl))
            cur_slug = slug
            inv = {"Up": 0.0, "Down": 0.0}
            cost = merged = 0.0
            mid_hist = []
            committed = None            # confirmed trend direction (hysteresis — no whipsaw)
            toks = _tokens(slug)
            print("== new window %s ==" % slug[-10:])
        if not toks:
            time.sleep(2)
            continue
        bbu, bau = _best(toks[0], "bids"), _best(toks[0], "asks")
        if bbu is not None and bau is not None:
            mid_hist.append((now, (bbu + bau) / 2))
        sig = chase_signal(mid_hist, now, LOOKBACK, THRESHOLD)
        # COMMIT to a direction once it's confirmed (its ask reached >=0.62) and hold it all
        # window — no whipsaw. After commit, the committed side is always the mover.
        if committed is None and sig is not None:
            sa = _best(toks[0] if sig == "Up" else toks[1], "asks")
            if sa is not None and sa >= 0.62:
                committed = sig
        drive = committed if committed is not None else sig
        if drive is not None:
            mover, fader = drive, ("Down" if drive == "Up" else "Up")
            ask = {"Up": _best(toks[0], "asks"), "Down": _best(toks[1], "asks")}
            am, af = ask[mover], ask[fader]
            took = []
            for side in (mover, fader):
                other = "Down" if side == "Up" else "Up"
                a = ask[side]
                if a is None or a <= 0 or a >= 0.99:
                    continue
                if side == mover and not (MOVER_MIN <= a <= CHASE_MAX):
                    continue                          # chase mover ONLY at a confirmed extreme
                if side == fader and a > FADER_MAX:
                    continue                          # take fader ONLY when deep-cheap
                if side == fader and inv[fader] >= inv[mover]:
                    continue                          # fader ONLY to pair the mover — never
                #                                       over-buy it (keeps residual net-long mover)
                if inv[side] - inv[other] >= RESID_CAP:
                    continue
                unit = SIZE * (a + taker_fee(a))
                if cost + unit > PWC:
                    continue
                inv[side] += SIZE
                cost += unit
                took.append("%s@%.3f(fee%.4f)" % (side, a, taker_fee(a)))
            mq = min(inv["Up"], inv["Down"])
            if mq > 0:
                inv["Up"] -= mq
                inv["Down"] -= mq
                merged += mq
            pc = (am + af) if (am and af) else None
            el = int(now) - int(slug.rsplit("-", 1)[1])
            print("  t=%3ds mid=%.3f CHASE %s | take [%s] | pair_cost=%s resid=%+.0f merged=%.0f cost=$%.2f" % (
                el, (bbu + bau) / 2 if bbu and bau else 0, mover,
                ", ".join(took) if took else "none",
                ("%.3f" % pc) if pc else "n/a", inv["Up"] - inv["Down"], merged, cost))
        time.sleep(2)


if __name__ == "__main__":
    main()
