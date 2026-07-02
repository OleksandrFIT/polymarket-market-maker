"""TOP-OF-BOOK MM policy on collected book data — THE strategy that reproduces the
competitor profile (+1.1% edge, 68% win, 97% matched on 197 windows). Bid best+1tick on
BOTH sides (price improvement -> alone at our level -> queue ahead = 0, legitimately),
inventory-skew cap on naked, merge matched pairs each tick, hold residual to resolution.
Includes stress: theta (share of crossing flow we capture), fee per share, size/cap,
4h-segment stability. Read-only, no trading.
Usage: python3 scripts/_top_book.py <book_jsonl>"""
import sys
import json
import collections

from quoter.research.mm_tape import load_window

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


def run(SIZE=5.0, CAP=10.0, theta=1.0, fee=0.0, hour_bucket=None):
    agg = collections.defaultdict(lambda: {"pnl": 0., "spent": 0., "n": 0, "win": 0,
                                           "merged": 0., "fills": 0.})
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
        for i, snap in enumerate(snaps):
            ts = snap["ts"]
            end = snaps[i + 1]["ts"] if i + 1 < len(snaps) else ts + 2
            for side, oi, book in (("Up", 0, snap["yes"]), ("Down", 1, snap["no"])):
                bids = book["bids"]
                asks = book["asks"]
                if not bids:
                    continue
                other = "Down" if side == "Up" else "Up"
                if inv[side] - inv[other] >= CAP:      # skew: don't grow naked
                    continue
                bb = max(float(p) for p, _ in bids)
                ba = min((float(p) for p, _ in asks), default=1.0)
                our = round(bb + TICK, 3)
                if our >= ba or our >= 0.99:            # never cross the spread
                    continue
                v = sum(t["size"] for t in st[oi]
                        if ts <= t["ts"] < end and t["side"] == "SELL" and t["price"] <= our)
                f = min(SIZE, v * theta)
                if f > 0:
                    inv[side] += f
                    spent += f * (our + fee)
                    fills += f
            m = min(inv["Up"], inv["Down"])
            if m > 0:                                   # merge matched pairs -> $1 each
                returned += m
                merged += m
                inv["Up"] -= m
                inv["Down"] -= m
        if spent <= 0:
            continue
        returned += inv[winner]                          # residual: winner pays, loser expires
        k = hour_bucket(slug) if hour_bucket else "all"
        a = agg[k]
        a["pnl"] += returned - spent
        a["spent"] += spent
        a["n"] += 1
        a["win"] += (returned - spent > 0)
        a["merged"] += merged
        a["fills"] += fills
    return agg


print("windows with book data: %d (resolved: %d)" % (len(byslug), len(WINS)))

a = run()["all"]
print("\n=== TOP-OF-BOOK baseline (size 5, cap 10, theta 1, fee 0) ===")
print("  edge %+0.2f%%  win-rate %.0f%%  matched %.0f%%  spent $%.0f  pnl $%+.1f  ($/win %+0.3f)" % (
    100 * a["pnl"] / a["spent"], 100 * a["win"] / a["n"],
    100 * 2 * a["merged"] / a["fills"] if a["fills"] else 0,
    a["spent"], a["pnl"], a["pnl"] / a["n"]))

print("\n=== theta (share of crossing flow captured; others improve too) ===")
for th in (1.0, 0.5, 0.25, 0.1):
    a = run(theta=th)["all"]
    print("  theta %.2f: edge %+0.2f%%  $/win %+0.3f  n=%d" % (th, 100 * a["pnl"] / a["spent"], a["pnl"] / a["n"], a["n"]))

print("\n=== fee per share (edge dies past ~0.6c — VERIFY real maker fee before live) ===")
for fee in (0.0, 0.002, 0.005, 0.01):
    a = run(fee=fee)["all"]
    print("  fee %.3f: edge %+0.2f%%" % (fee, 100 * a["pnl"] / a["spent"]))

print("\n=== size/cap scaling ===")
for sz, cap in ((5, 10), (10, 20), (25, 50)):
    a = run(SIZE=sz, CAP=cap)["all"]
    print("  size %2d cap %2d: edge %+0.2f%%  $/win %+0.3f" % (sz, cap, 100 * a["pnl"] / a["spent"], a["pnl"] / a["n"]))

print("\n=== stability by 4h segment ===")
def hb(slug):
    ts = int(slug.rsplit("-", 1)[1])
    return "seg%d" % ((ts // 14400) % 6)
segs = run(hour_bucket=hb)
for k in sorted(segs):
    a = segs[k]
    print("  %s: edge %+0.2f%%  n=%d  win%% %.0f" % (k, 100 * a["pnl"] / a["spent"], a["n"], 100 * a["win"] / a["n"]))
