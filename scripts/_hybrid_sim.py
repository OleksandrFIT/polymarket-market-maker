"""READ-ONLY LIVE PAPER sim of the HYBRID (0xb27b ~53/47) tactic. NOT LIVE — places NO orders.

Model (user's): on a momentum signal each tick —
  TAKER the MOVER (winner) at its ASK (real fill, we are the aggressor, + taker fee), AND
  MAKER the FADER (loser) at its BID — assumed ALWAYS filled, because the losing side falls into
  our resting bid (adverse selection turned into the CHEAP leg). No maker fee.
Pair = cheap loser (~0.10) + chased winner (~0.88) ~= 0.98 (his pair). Merge every tick, NEVER
sell, residual rides to resolution. Headline metric = naked WIN-RATE (does our residual become a
fair coin ~50% like his 55%, or stay a biased loser?).

FIDELITY: taker/winner leg = decision-grade (real ask). maker/loser leg = ASSUMED-filled (the
user's "loser always fills our bid" model) — defensible but the real fill rate is only knowable
LIVE. This is a forward paper sim on fresh windows, NOT a live test.

Usage: python3 scripts/_hybrid_sim.py [minutes]"""
import sys
import json
import time
import urllib.request

from quoter.runner.top_book_planner import taker_fee

WINDOW_SEC = 300
GAMMA, CLOB = "https://gamma-api.polymarket.com", "https://clob.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (hybrid-sim)"}
SIZE, RESID_CAP, PWC = 5.0, 8.0, 15.0
LOOKBACK, THRESHOLD = 30.0, 0.03
RUN_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0


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


def _chase(mid_hist, now, lookback, thr):
    if not mid_hist:
        return None
    cur = mid_hist[-1][1]
    cutoff = now - lookback
    past = None
    for ts, m in mid_hist:
        if ts <= cutoff:
            past = m
        else:
            break
    if past is None:
        past = mid_hist[0][1]
    d = cur - past
    return "Up" if d >= thr else ("Down" if d <= -thr else None)


def main():
    end = time.time() + RUN_MIN * 60
    cur, toks, last_mid = None, None, 0.5
    inv = {"Up": 0.0, "Down": 0.0}
    held = {"Up": 0.0, "Down": 0.0}
    cost = merged = merged_cost = 0.0
    mid_hist = []
    tot = {"pnl": 0.0, "n": 0, "won": 0, "lost": 0, "pcsum": 0.0, "pcn": 0}
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
                pc = (merged_cost / merged) if merged > 0 else None
                out = "WON" if (rs != "flat" and rs == win) else ("LOST" if rs != "flat" else "-")
                tot["pnl"] += pnl
                tot["n"] += 1
                if out == "WON":
                    tot["won"] += 1
                elif out == "LOST":
                    tot["lost"] += 1
                if pc is not None:
                    tot["pcsum"] += pc
                    tot["pcn"] += 1
                wr = 100 * tot["won"] / max(tot["won"] + tot["lost"], 1)
                print("  [%s done] win=%s merged=%.0f pair=%s resid=%s%.0f(%s) PnL=$%+.2f | "
                      "CUM n=%d PnL=$%+.2f naked-WR=%.0f%% (W%d/L%d) avgpair=%.3f" % (
                          cur[-10:], win, merged, ("%.3f" % pc) if pc is not None else "n/a",
                          rs, abs(net), out, pnl,
                          tot["n"], tot["pnl"], wr, tot["won"], tot["lost"],
                          tot["pcsum"] / max(tot["pcn"], 1)))
            cur = slug
            inv = {"Up": 0.0, "Down": 0.0}
            held = {"Up": 0.0, "Down": 0.0}
            cost = merged = merged_cost = 0.0
            mid_hist = []
            toks = _tokens(slug)
            print("== new window %s ==" % slug[-10:])
        if not toks:
            time.sleep(2)
            continue
        au, aa = _best(toks[0], "bids"), _best(toks[0], "asks")
        du, da = _best(toks[1], "bids"), _best(toks[1], "asks")
        ask = {"Up": aa, "Down": da}
        bid = {"Up": au, "Down": du}
        last_mid = ((au + aa) / 2) if (au is not None and aa is not None) else last_mid
        mid_hist.append((now, last_mid))
        sig = _chase(mid_hist, now, LOOKBACK, THRESHOLD)
        if sig is not None:
            fade = "Down" if sig == "Up" else "Up"
            a = ask[sig]                                  # TAKER the mover/winner at ask (real)
            if a is not None and 0 < a < 0.99 and inv[sig] - inv[fade] < RESID_CAP:
                unit = SIZE * (a + taker_fee(a))
                if cost + unit <= PWC:
                    inv[sig] += SIZE
                    held[sig] += SIZE * a
                    cost += unit
            fb = bid[fade]                                # MAKER the fader/loser at bid (assumed fill)
            if fb is not None and 0 < fb < 0.99 and inv[fade] - inv[sig] < RESID_CAP:
                if cost + SIZE * fb <= PWC:
                    inv[fade] += SIZE
                    held[fade] += SIZE * fb
                    cost += SIZE * fb
        mq = min(inv["Up"], inv["Down"])                 # merge matched pairs each tick
        if mq > 0:
            avg_up = held["Up"] / inv["Up"] if inv["Up"] > 0 else 0.0
            avg_dn = held["Down"] / inv["Down"] if inv["Down"] > 0 else 0.0
            merged_cost += mq * (avg_up + avg_dn)
            for s in ("Up", "Down"):
                a = held[s] / inv[s] if inv[s] > 0 else 0.0
                held[s] = max(0.0, held[s] - mq * a)
                inv[s] -= mq
            merged += mq
        time.sleep(2)


if __name__ == "__main__":
    main()
