"""Asymmetric maker-tilt backtest (the user's insight): on causal momentum, bid the FADER
(losing side) LOW — it still fills when it crashes (everyone dumps it) but at a cheap cost
basis, so a loser-residual costs little; bid the MOVER (winning side) near market to catch its
pullbacks (the only sell-flow on it). NEVER sell — hold residual to resolution (winner redeems
$1, loser expires). All maker (no taker fee). Compare vs symmetric passive best+tick.
Reuses _top_book's fill model (fill SELL prints with price <= our bid). Read-only.
Run on server: POLY_MM_CACHE=/home/ubuntu/cache_poly_mm python3 scripts/_tilt.py <book_jsonl>"""
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
    return ba if bb is None else (bb if ba is None else (bb + ba) / 2)


def run(SIZE=5.0, CAP=10.0, theta=1.0, tilt=False,
        lookback=40, threshold=0.04, fade_bid=0.10, mover_reach=0.0):
    agg = {"pnl": 0., "spent": 0., "n": 0, "win": 0, "merged": 0., "fills": 0.,
           "adv": 0., "wintilt": 0, "cost_loser": 0., "sh_loser": 0.}
    for slug, snaps in byslug.items():
        if slug not in WINS:
            continue
        tape, winner, _ = WINS[slug]
        loser = "Down" if winner == "Up" else "Up"
        st = {0: [t for t in tape if t["oi"] == 0], 1: [t for t in tape if t["oi"] == 1]}
        inv = {"Up": 0., "Down": 0.}
        cost = {"Up": 0., "Down": 0.}
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
            sig = chase_signal(mid_hist, ts, lookback, threshold) if tilt else None
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
                if tilt and sig is not None and side != sig:
                    our = round(fade_bid, 3)                      # FADER: bid low & cheap
                elif tilt and sig is not None and side == sig:
                    our = round(min(ba - TICK, bb + TICK + mover_reach), 3)  # MOVER: reach up
                else:
                    our = round(bb + TICK, 3)                     # neutral / passive
                if our <= 0 or our >= ba or our >= 0.99:
                    continue
                v = sum(t["size"] for t in st[oi]
                        if ts <= t["ts"] < end and t["side"] == "SELL" and t["price"] <= our)
                f = min(SIZE, v * theta)
                if f > 0:
                    inv[side] += f
                    cost[side] += f * our
                    spent += f * our
                    fills += f
            m = min(inv["Up"], inv["Down"])
            if m > 0:
                returned += m
                merged += m
                inv["Up"] -= m
                inv["Down"] -= m
        if spent <= 0:
            continue
        returned += inv[winner]                                  # winner residual redeems; loser expires
        agg["adv"] += inv[loser]
        agg["cost_loser"] += cost[loser] - (cost[loser] / (inv[loser] if False else 1))  # placeholder
        if inv[loser] > 0.5:
            agg["sh_loser"] += inv[loser]
        agg["wintilt"] += 1 if (inv[winner] - inv[loser]) > 0.5 else 0
        agg["pnl"] += returned - spent
        agg["spent"] += spent
        agg["n"] += 1
        agg["win"] += (returned - spent > 0)
        agg["merged"] += merged
        agg["fills"] += fills
    return agg


def show(tag, a):
    if a["n"] == 0 or a["spent"] <= 0:
        print("  %-30s no data" % tag)
        return
    print("  %-30s edge %+0.2f%%  win %2.0f%%  matched %2.0f%%  adv %.1f sh  net-long-winner %2.0f%%  $/win %+0.3f" % (
        tag, 100 * a["pnl"] / a["spent"], 100 * a["win"] / a["n"],
        100 * 2 * a["merged"] / a["fills"] if a["fills"] else 0,
        a["adv"] / a["n"], 100 * a["wintilt"] / a["n"], a["pnl"] / a["n"]))


print("windows: %d (resolved %d)\n" % (len(byslug), len(WINS)))
print("=== passive (symmetric best+tick, sells nothing here either) ===")
show("passive", run())
print("\n=== asymmetric tilt (fader low / mover reaches, never-sell) ===")
for fb in (0.05, 0.10, 0.20):
    for mr in (0.0, 0.05, 0.10):
        show("tilt fade%.2f reach%.2f" % (fb, mr), run(tilt=True, fade_bid=fb, mover_reach=mr))
