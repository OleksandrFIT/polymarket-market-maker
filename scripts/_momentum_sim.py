"""READ-ONLY live sim — v8 (both-sides leader model, the version before the hybrid). Each tick,
buy EITHER side if it is LEADING (ask in [LEAD_MIN, LEAD_MAX] — accumulate the winner-candidate)
OR CRASHED-CHEAP (ask <= CHEAP_MAX AND only to PAIR a leader we already hold — never net-long the
cheap loser alone). Two-sided, hedged. Merge pairs; hold net residual to resolution. All TAKER
(fill at ask -> no queue/adverse-selection, decision-grade). Places NO orders.
Usage: python3 scripts/_momentum_sim.py [minutes]"""
import sys
import json
import time
import urllib.request

from quoter.runner.top_book_planner import taker_fee

WINDOW_SEC = 300
GAMMA, CLOB = "https://gamma-api.polymarket.com", "https://clob.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (mom-sim)"}
LEAD_MIN, LEAD_MAX, CHEAP_MAX = 0.55, 0.90, 0.15
SIZE, RESID_CAP, PWC = 5.0, 8.0, 20.0
RUN_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0


def _get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10))


def _tokens(slug):
    m = _get("%s/markets?slug=%s" % (GAMMA, slug))
    if not m:
        return None
    t = json.loads(m[0].get("clobTokenIds", "[]"))
    return (t[0], t[1]) if len(t) == 2 else None


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
    cur = None
    inv = {"Up": 0.0, "Down": 0.0}
    cost = merged = 0.0
    last_mid = 0.5
    toks = None
    while time.time() < end:
        now = time.time()
        slug = "btc-updown-5m-%d" % (int(now) // WINDOW_SEC * WINDOW_SEC)
        if slug != cur:
            if cur:
                win = "Up" if last_mid >= 0.5 else "Down"
                net = inv["Up"] - inv["Down"]
                rs = "Up" if net > 0 else ("Down" if net < 0 else "flat")
                rpay = abs(net) if rs == win else 0.0
                pnl = merged + rpay - cost
                print("  [%s done] winner=%s | merged=%.0f resid=%s%.0f(%s) | cost=$%.2f  SIM-PnL=$%+.2f" % (
                    cur[-10:], win, merged, rs, abs(net),
                    "WON" if rs == win else ("LOST" if rs != "flat" else "-"), cost, pnl))
            cur = slug
            inv = {"Up": 0.0, "Down": 0.0}
            cost = merged = 0.0
            toks = _tokens(slug)
            print("== new window %s ==" % slug[-10:])
        if not toks:
            time.sleep(2)
            continue
        ask = {"Up": _best(toks[0], "asks"), "Down": _best(toks[1], "asks")}
        au = _best(toks[0], "bids")
        last_mid = ((au + ask["Up"]) / 2) if (au is not None and ask["Up"] is not None) else last_mid
        took = []
        for side in ("Up", "Down"):
            other = "Down" if side == "Up" else "Up"
            a = ask[side]
            if a is None or a <= 0 or a >= 0.99:
                continue
            is_lead = LEAD_MIN <= a <= LEAD_MAX
            is_cheap = a <= CHEAP_MAX and inv[other] > inv[side]
            if not (is_lead or is_cheap):
                continue
            if inv[side] - inv[other] >= RESID_CAP:
                continue
            unit = SIZE * (a + taker_fee(a))
            if cost + unit > PWC:
                continue
            inv[side] += SIZE
            cost += unit
            took.append("%s@%.3f%s" % (side, a, "L" if is_lead else "C"))
        mq = min(inv["Up"], inv["Down"])
        if mq > 0:
            inv["Up"] -= mq
            inv["Down"] -= mq
            merged += mq
        if took:
            el = int(now) - int(slug.rsplit("-", 1)[1])
            print("  t=%3ds mid=%.3f take [%s] resid=%+.0f merged=%.0f cost=$%.2f" % (
                el, last_mid, ", ".join(took), inv["Up"] - inv["Down"], merged, cost))
        time.sleep(2)


if __name__ == "__main__":
    main()
