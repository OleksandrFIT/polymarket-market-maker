"""READ-ONLY live sim — A/B: v8 (both-sides leader) vs v9 (pair-first) on the SAME ticks.
v9 = v8 minus the stranded-residual tail: (1) lead-take only when resid<=0 (max one unpaired
clip); (2) time-lock: a side unpaired >LOCK_SEC stops getting NEW lead-buys this window;
(3) rescue-pair: unpaired >RESCUE_SEC may pair above CHEAP_MAX if pair cost (vwap of the
unpaired side + ask + fee) <= RESCUE_PAIR_MAX — insurance floor instead of a full loss.
Both portfolios see identical books. All TAKER (fill at ask, decision-grade). NO orders.
Usage: python3 scripts/_momentum_sim9.py [minutes]"""
import sys
import json
import time
import urllib.request

from quoter.runner.top_book_planner import taker_fee

WINDOW_SEC = 300
GAMMA, CLOB = "https://gamma-api.polymarket.com", "https://clob.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (mom-sim9)"}
LEAD_MIN, LEAD_MAX, CHEAP_MAX = 0.55, 0.90, 0.15
SIZE, PWC = 5.0, 20.0
V8_RESID_CAP = 8.0
LOCK_SEC, RESCUE_SEC, RESCUE_PAIR_MAX = 45.0, 60.0, 0.99
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


def _fresh():
    return {"inv": {"Up": 0.0, "Down": 0.0}, "cost": 0.0, "merged": 0.0,
            "buys": {"Up": [], "Down": []},           # (price, size) per side, for vwap
            "unpaired_since": {"Up": None, "Down": None}, "locked": {"Up": False, "Down": False}}


def _vwap(p, side):
    b = p["buys"][side]
    sh = sum(s for _, s in b)
    return (sum(pr * s for pr, s in b) / sh) if sh else 0.0


def _buy(p, side, a):
    p["inv"][side] += SIZE
    p["cost"] += SIZE * (a + taker_fee(a))
    p["buys"][side].append((a, SIZE))


def _merge_and_track(p, now):
    mq = min(p["inv"]["Up"], p["inv"]["Down"])
    if mq > 0:
        p["inv"]["Up"] -= mq
        p["inv"]["Down"] -= mq
        p["merged"] += mq
    for side in ("Up", "Down"):
        other = "Down" if side == "Up" else "Up"
        if p["inv"][side] > p["inv"][other]:
            if p["unpaired_since"][side] is None:
                p["unpaired_since"][side] = now
        else:
            p["unpaired_since"][side] = None


def _pnl(p, win):
    net = p["inv"]["Up"] - p["inv"]["Down"]
    rs = "Up" if net > 0 else ("Down" if net < 0 else "flat")
    rpay = abs(net) if rs == win else 0.0
    return p["merged"] + rpay - p["cost"], rs, abs(net)


def main():
    end = time.time() + RUN_MIN * 60
    cur, toks, last_mid = None, None, 0.5
    v8, v9 = _fresh(), _fresh()
    tot = {"v8": 0.0, "v9": 0.0, "n": 0}
    while time.time() < end:
        now = time.time()
        slug = "btc-updown-5m-%d" % (int(now) // WINDOW_SEC * WINDOW_SEC)
        if slug != cur:
            if cur:
                win = "Up" if last_mid >= 0.5 else "Down"
                p8, rs8, rz8 = _pnl(v8, win)
                p9, rs9, rz9 = _pnl(v9, win)
                tot["v8"] += p8; tot["v9"] += p9; tot["n"] += 1
                print("  [%s done] winner=%s | v8: mrg=%.0f resid=%s%.0f cost=$%.2f PnL=$%+.2f | "
                      "v9: mrg=%.0f resid=%s%.0f cost=$%.2f PnL=$%+.2f" % (
                          cur[-10:], win, v8["merged"], rs8, rz8, v8["cost"], p8,
                          v9["merged"], rs9, rz9, v9["cost"], p9))
                print("  [cum n=%d] v8=$%+.2f v9=$%+.2f" % (tot["n"], tot["v8"], tot["v9"]))
            cur, v8, v9 = slug, _fresh(), _fresh()
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
            unit = SIZE * (a + taker_fee(a))
            # ── v8 (контроль): як у нічного прогону ──
            is_cheap8 = a <= CHEAP_MAX and v8["inv"][other] > v8["inv"][side]
            if ((is_lead or is_cheap8) and v8["inv"][side] - v8["inv"][other] < V8_RESID_CAP
                    and v8["cost"] + unit <= PWC):
                _buy(v8, side, a)
                took.append("v8:%s@%.3f%s" % (side, a, "L" if is_lead else "C"))
            # ── v9 (pair-first) ──
            us_o = v9["unpaired_since"][other]
            if v9["unpaired_since"][side] is not None and now - v9["unpaired_since"][side] > LOCK_SEC:
                v9["locked"][side] = True
            pairs_resid = v9["inv"][other] > v9["inv"][side]
            is_cheap9 = a <= CHEAP_MAX and pairs_resid
            is_rescue = (pairs_resid and us_o is not None and now - us_o > RESCUE_SEC
                         and _vwap(v9, other) + a + taker_fee(a) <= RESCUE_PAIR_MAX)
            lead_ok = is_lead and v9["inv"][side] <= v9["inv"][other] and not v9["locked"][side]
            if (lead_ok or is_cheap9 or is_rescue) and v9["cost"] + unit <= PWC:
                _buy(v9, side, a)
                took.append("v9:%s@%.3f%s" % (side, a, "L" if lead_ok else ("R" if is_rescue and not is_cheap9 else "C")))
        _merge_and_track(v8, now)
        _merge_and_track(v9, now)
        if took:
            el = int(now) - int(slug.rsplit("-", 1)[1])
            print("  t=%3ds mid=%.3f take [%s] | v8 resid=%+.0f cost=$%.2f | v9 resid=%+.0f cost=$%.2f%s" % (
                el, last_mid, ", ".join(took),
                v8["inv"]["Up"] - v8["inv"]["Down"], v8["cost"],
                v9["inv"]["Up"] - v9["inv"]["Down"], v9["cost"],
                " LOCK:" + "/".join(s for s in ("Up", "Down") if v9["locked"][s]) if any(v9["locked"].values()) else ""))
        time.sleep(2)


if __name__ == "__main__":
    main()
