"""Momentum-chase backtest: passive top-of-book + causal chase of the winning side.
Reuses _top_book.py's fill model (bid best+tick, fill SELL prints <= our bid, merge,
hold residual). Adds: take the rising side at its ask (chase), and an `adverse` metric
(loser shares held to resolution). Read-only.
Memory: run on SLICES on the 1.9G server; set POLY_MM_CACHE=/home/ubuntu/cache_poly_mm.
Usage: python3 scripts/_chase.py <book_jsonl>"""
import sys
import json
import collections

from quoter.research.mm_tape import load_window
from quoter.research.chase import chase_signal

BOOK = sys.argv[1] if len(sys.argv) > 1 else "/tmp/book_all.jsonl"
TICK = 0.001

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

WINS = {}
for slug in byslug:
    w = load_window(slug)
    if w and w[0]:
        WINS[slug] = w


def _mid(book):
    bb = max((float(p) for p, _ in book["bids"]), default=None)
    ba = min((float(p) for p, _ in book["asks"]), default=None)
    if bb is None and ba is None:
        return None
    if bb is None:
        return ba
    if ba is None:
        return bb
    return (bb + ba) / 2


def run(SIZE=5.0, CAP=10.0, theta=1.0, fee=0.0,
        chase=False, lookback=40, threshold=0.05, chase_max=0.85, chase_size=5.0):
    agg = {"pnl": 0., "spent": 0., "n": 0, "win": 0, "merged": 0., "fills": 0., "adverse": 0.}
    for slug, snaps in byslug.items():
        if slug not in WINS:
            continue
        tape, winner, _ = WINS[slug]
        st = {0: [t for t in tape if t["oi"] == 0], 1: [t for t in tape if t["oi"] == 1]}
        inv = {"Up": 0., "Down": 0.}
        spent = 0.
        returned = 0.
        fills = 0.
        merged = 0.
        mid_hist = []
        for i, snap in enumerate(snaps):
            ts = snap["ts"]
            end = snaps[i + 1]["ts"] if i + 1 < len(snaps) else ts + 2
            um = _mid(snap["yes"])
            if um is not None:
                mid_hist.append((ts, um))
            # passive maker fills — identical to _top_book.py
            for side, oi, book in (("Up", 0, snap["yes"]), ("Down", 1, snap["no"])):
                bids = book["bids"]
                asks = book["asks"]
                if not bids:
                    continue
                other = "Down" if side == "Up" else "Up"
                if inv[side] - inv[other] >= CAP:
                    continue
                bb = max(float(p) for p, _ in bids)
                ba = min((float(p) for p, _ in asks), default=1.0)
                our = round(bb + TICK, 3)
                if our >= ba or our >= 0.99:
                    continue
                v = sum(t["size"] for t in st[oi]
                        if ts <= t["ts"] < end and t["side"] == "SELL" and t["price"] <= our)
                f = min(SIZE, v * theta)
                if f > 0:
                    inv[side] += f
                    spent += f * (our + fee)
                    fills += f
            # chase — TAKE the rising (winning-favorite) side at its ask, bounded
            if chase:
                sig = chase_signal(mid_hist, ts, lookback, threshold)
                if sig is not None:
                    other = "Down" if sig == "Up" else "Up"
                    book = snap["yes"] if sig == "Up" else snap["no"]
                    ba = min((float(p) for p, _ in book["asks"]), default=None)
                    if ba is not None and ba <= chase_max and inv[sig] - inv[other] < CAP:
                        f = min(chase_size, CAP - (inv[sig] - inv[other]))
                        if f > 0:
                            inv[sig] += f
                            spent += f * (ba + fee)
                            fills += f
            # merge matched pairs -> $1 each
            m = min(inv["Up"], inv["Down"])
            if m > 0:
                returned += m
                merged += m
                inv["Up"] -= m
                inv["Down"] -= m
        if spent <= 0:
            continue
        returned += inv[winner]                       # residual winner pays $1
        loser = "Down" if winner == "Up" else "Up"
        agg["adverse"] += inv[loser]                  # loser residual = adverse selection
        agg["pnl"] += returned - spent
        agg["spent"] += spent
        agg["n"] += 1
        agg["win"] += (returned - spent > 0)
        agg["merged"] += merged
        agg["fills"] += fills
    return agg


def _fmt(tag, a):
    if a["n"] == 0 or a["spent"] <= 0:
        print("  %-30s no data" % tag)
        return
    print("  %-30s edge %+0.2f%%  win %.0f%%  matched %.0f%%  adverse %.1f sh/win  $/win %+0.3f  n=%d" % (
        tag, 100 * a["pnl"] / a["spent"], 100 * a["win"] / a["n"],
        100 * 2 * a["merged"] / a["fills"] if a["fills"] else 0,
        a["adverse"] / a["n"], a["pnl"] / a["n"], a["n"]))


print("windows: %d (resolved %d)" % (len(byslug), len(WINS)))

print("\n=== PASSIVE vs CHASE (size 5, cap 10, theta 1, fee 0) ===")
_fmt("passive", run())
_fmt("chase(lb40,thr.05,max.85)", run(chase=True))

print("\n=== chase param sweep (fee 0) ===")
for lb in (20, 40, 60):
    for thr in (0.03, 0.05, 0.08):
        for cmax in (0.80, 0.85, 0.90):
            _fmt("chase lb%d thr%.2f max%.2f" % (lb, thr, cmax),
                 run(chase=True, lookback=lb, threshold=thr, chase_max=cmax))

print("\n=== fee gate (edge dies past ~0.5c on passive; check chase too) ===")
for fee in (0.0, 0.002, 0.005):
    _fmt("passive fee%.3f" % fee, run(fee=fee))
    _fmt("chase   fee%.3f" % fee, run(chase=True, fee=fee))
