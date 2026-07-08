"""0xb27b's DECODED system, modeled correctly: reactive momentum + merge wrapper, TAKER at
extremes. When a side has upward momentum, TAKE it at the ask (chase the winner up toward 0.9+);
TAKE the fading side cheap at its ask (loser 0.02-0.11); merge pairs; net-long-winner residual
redeems $1. Uses the REAL Polymarket taker fee curve: fee/share = 1.80% * min(price, 1-price)
(peaks at 0.50, ~0 at extremes) — the thing the old _chase.py got wrong. Compare vs passive.
Run: POLY_MM_CACHE=/home/ubuntu/cache_poly_mm python3 scripts/_momentum.py <book_jsonl>"""
import sys
import json
import collections

from quoter.research.mm_tape import load_window
from quoter.research.chase import chase_signal

BOOK = sys.argv[1] if len(sys.argv) > 1 else "/tmp/book_all.jsonl"
TICK = 0.001
FEE_RATE = 0.018                                        # crypto taker; peaks at 0.50


def fee(p):
    return FEE_RATE * min(p, 1 - p)


byslug = collections.defaultdict(list)
for l in open(BOOK):
    l = l.strip()
    if not l:
        continue
    try:
        r = json.loads(l)
    except json.JSONDecodeError:
        continue
    byslug[r["slug"]].append(r)
for s in byslug.values():
    s.sort(key=lambda x: x["ts"])

WINS = {slug: w for slug in byslug if (w := load_window(slug)) and w[0]}


def _mid(book):
    bb = max((float(p) for p, _ in book["bids"]), default=None)
    ba = min((float(p) for p, _ in book["asks"]), default=None)
    if bb is None and ba is None:
        return None
    return ba if bb is None else (bb if ba is None else (bb + ba) / 2)


def _ask(book):
    a = book["asks"]
    if not a:
        return None, 0.0
    p, sz = min(((float(p), float(s)) for p, s in a), key=lambda x: x[0])
    return p, sz


def run(mode="passive", CAP=10.0, theta=1.0, SIZE=5.0,
        lookback=30, threshold=0.03, chase_sz=10.0, budget=40.0):
    agg = {"pnl": 0., "spent": 0., "n": 0, "win": 0, "merged": 0., "worst": 0.}
    for slug, snaps in byslug.items():
        if slug not in WINS:
            continue
        tape, winner, _ = WINS[slug]
        loser = "Down" if winner == "Up" else "Up"
        st = {0: [t for t in tape if t["oi"] == 0 and t["side"] == "SELL"],
              1: [t for t in tape if t["oi"] == 1 and t["side"] == "SELL"]}
        inv = {"Up": 0., "Down": 0.}
        spent = returned = merged = 0.
        mid_hist = []
        for i, snap in enumerate(snaps):
            ts = snap["ts"]
            end = snaps[i + 1]["ts"] if i + 1 < len(snaps) else ts + 2
            m = _mid(snap["yes"])
            if m is not None:
                mid_hist.append((ts, m))
            if mode == "passive":
                # symmetric maker best+tick, fill vs SELL prints <= bid (fee 0 maker)
                for side, oi, book in (("Up", 0, snap["yes"]), ("Down", 1, snap["no"])):
                    bids = book["bids"]
                    if not bids:
                        continue
                    other = "Down" if side == "Up" else "Up"
                    if inv[side] - inv[other] >= CAP:
                        continue
                    bb = max(float(p) for p, _ in bids)
                    ba, _sz = _ask(book)
                    our = round(bb + TICK, 3)
                    if ba is None or our >= ba or our >= 0.99:
                        continue
                    v = sum(t["size"] for t in st[oi] if ts <= t["ts"] < end and t["price"] <= our)
                    f = min(SIZE, v * theta)
                    if f > 0:
                        inv[side] += f
                        spent += f * our
            else:  # momentum-take: chase the mover + take the fader cheap, both TAKER at ask
                sig = chase_signal(mid_hist, ts, lookback, threshold)
                if sig is not None and spent < budget * 3:
                    fade = "Down" if sig == "Up" else "Up"
                    for side, book, is_mover in ((sig, snap["yes"] if sig == "Up" else snap["no"], True),
                                                 (fade, snap["yes"] if fade == "Up" else snap["no"], False)):
                        other = "Down" if side == "Up" else "Up"
                        if inv[side] - inv[other] >= CAP:
                            continue
                        ap, asz = _ask(book)
                        if ap is None or ap >= 0.99 or ap <= 0:
                            continue
                        f = min(chase_sz, asz)
                        if f > 0:
                            inv[side] += f
                            spent += f * (ap + fee(ap))
            mm = min(inv["Up"], inv["Down"])
            if mm > 0:
                returned += mm
                merged += mm
                inv["Up"] -= mm
                inv["Down"] -= mm
        if spent <= 0:
            continue
        returned += inv[winner]
        wp = returned - spent
        agg["worst"] = min(agg["worst"], wp)
        agg["pnl"] += wp
        agg["spent"] += spent
        agg["n"] += 1
        agg["win"] += (wp > 0)
        agg["merged"] += merged
    return agg


def show(tag, a):
    if a["n"] == 0 or a["spent"] <= 0:
        print("  %-34s no data" % tag)
        return
    print("  %-34s edge %+0.2f%%  win %2.0f%%  $/win %+0.3f  worst-win $%+.1f  n=%d" % (
        tag, 100 * a["pnl"] / a["spent"], 100 * a["win"] / a["n"], a["pnl"] / a["n"], a["worst"], a["n"]))


print("windows: %d\n" % len(WINS))
show("passive (maker, fee0)", run(mode="passive"))
print()
for th in (0.02, 0.04, 0.06):
    for lb in (20, 40):
        show("momentum thr%.2f lb%d" % (th, lb), run(mode="momentum", threshold=th, lookback=lb))
