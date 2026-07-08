"""Toxic-flow-aware backtest — models ADVERSE SELECTION, the thing the naive fill model misses.
Each SELL print is classified TOXIC (the side's price falls over the next DELTA sec -> informed
dump) or BENIGN (price holds/rises -> uninformed, e.g. a winner pullback). A slow passive MM
loses the benign flow to faster/better-queued MMs but gets stuck with the toxic flow: so we fill
`toxic_frac` (~all) of toxic prints but only `benign_frac` (small) of benign prints. That
asymmetry = adverse selection, and it should flip passive from the naive model's net-long-winner
to LIVE's net-long-loser / breakeven-negative. VALIDATE that first; only then trust it to judge
the tilt / never-sell ideas.
Run on server: POLY_MM_CACHE=/home/ubuntu/cache_poly_mm python3 scripts/_toxic.py <book_jsonl>"""
import sys
import json
import bisect
import collections

from quoter.research.mm_tape import load_window
from quoter.research.chase import chase_signal

BOOK = sys.argv[1] if len(sys.argv) > 1 else "/tmp/book_all.jsonl"
TICK = 0.001
DELTA = 25                                              # forward window (sec) for toxicity

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
TL = {}                                                 # slug -> (ts_arr, upmid_arr) forward-price timeline
for slug, snaps in byslug.items():
    w = load_window(slug)
    if not (w and w[0]):
        continue
    WINS[slug] = w
    tsa, mida = [], []
    for sn in snaps:
        bb = max((float(p) for p, _ in sn["yes"]["bids"]), default=None)
        ba = min((float(p) for p, _ in sn["yes"]["asks"]), default=None)
        m = None if (bb is None and ba is None) else (ba if bb is None else (bb if ba is None else (bb + ba) / 2))
        if m is not None:
            tsa.append(sn["ts"])
            mida.append(m)
    TL[slug] = (tsa, mida)


def fwd_upmid(slug, t):
    tsa, mida = TL[slug]
    if not tsa:
        return None
    i = bisect.bisect_left(tsa, t)
    if i >= len(tsa):
        i = len(tsa) - 1
    return mida[i]


def run(SIZE=5.0, CAP=10.0, benign=0.25, toxic=1.0, tilt=False, sell=True,
        lookback=40, threshold=0.04, fade_bid=0.10, mover_reach=0.0,
        levels=1, spacing=0.02):
    agg = {"pnl": 0., "spent": 0., "n": 0, "win": 0, "merged": 0., "fills": 0., "adv": 0., "wtilt": 0}
    for slug, snaps in byslug.items():
        if slug not in WINS:
            continue
        tape, winner, _ = WINS[slug]
        loser = "Down" if winner == "Up" else "Up"
        st = {0: [t for t in tape if t["oi"] == 0 and t["side"] == "SELL"],
              1: [t for t in tape if t["oi"] == 1 and t["side"] == "SELL"]}
        inv = {"Up": 0., "Down": 0.}
        spent = returned = fills = merged = 0.
        mid_hist = []
        for i, snap in enumerate(snaps):
            ts = snap["ts"]
            end = snaps[i + 1]["ts"] if i + 1 < len(snaps) else ts + 2
            bbu = max((float(p) for p, _ in snap["yes"]["bids"]), default=None)
            bau = min((float(p) for p, _ in snap["yes"]["asks"]), default=1.0)
            if bbu is not None:
                mid_hist.append((ts, (bbu + bau) / 2))
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
                    top = round(fade_bid, 3); nlev = 1
                elif tilt and sig is not None and side == sig:
                    top = round(min(ba - TICK, bb + TICK + mover_reach), 3); nlev = 1
                else:
                    top = round(bb + TICK, 3); nlev = levels
                lvl = [round(top - k * spacing, 3) for k in range(nlev)]
                lvl = [p for p in lvl if 0 < p < ba and p < 0.99]      # descending prices
                if not lvl:
                    continue
                lvl_fill = [0.0] * len(lvl)
                for t in st[oi]:
                    if not (ts <= t["ts"] < end):
                        continue
                    P = t["price"]
                    li = next((k for k, lp in enumerate(lvl) if lp >= P), None)  # highest bid >= P
                    if li is None:
                        continue
                    fwd = fwd_upmid(slug, t["ts"] + DELTA)
                    if fwd is None:
                        frac = benign
                    else:
                        side_fwd = fwd if side == "Up" else (1 - fwd)
                        frac = toxic if (side_fwd < P - 0.005) else benign  # side fell after -> toxic
                    lvl_fill[li] += frac * t["size"]
                for k, lp in enumerate(lvl):
                    if inv[side] - inv[other] >= CAP:
                        break
                    f = min(SIZE, lvl_fill[k])
                    if f > 0:
                        inv[side] += f
                        spent += f * lp
                        fills += f
            m = min(inv["Up"], inv["Down"])
            if m > 0:
                returned += m
                merged += m
                inv["Up"] -= m
                inv["Down"] -= m
        # near-end SELL of naked loser (if enabled) at last book bid, else hold to resolution
        if sell:
            naked = inv["Up"] - inv["Down"]
            if abs(naked) > 0.5:
                heavy = "Up" if naked > 0 else "Down"
                lastbk = snaps[-1]["yes" if heavy == "Up" else "no"]
                hb = max((float(p) for p, _ in lastbk["bids"]), default=0.0)
                returned += abs(naked) * hb                # sold the naked loser into its bid
                inv[heavy] -= abs(naked)
        if spent <= 0:
            continue
        returned += max(inv[winner], 0.0)                # winner residual redeems; loser expires
        agg["adv"] += max(inv[loser], 0.0)
        agg["wtilt"] += 1 if (inv[winner] - inv[loser]) > 0.5 else 0
        agg["pnl"] += returned - spent
        agg["spent"] += spent
        agg["n"] += 1
        agg["win"] += (returned - spent > 0)
        agg["merged"] += merged
        agg["fills"] += fills
    return agg


def show(tag, a):
    if a["n"] == 0 or a["spent"] <= 0:
        print("  %-32s no data" % tag)
        return
    print("  %-32s edge %+0.2f%%  win %2.0f%%  matched %2.0f%%  adv %.1f  net-long-win %2.0f%%  $/win %+0.3f" % (
        tag, 100 * a["pnl"] / a["spent"], 100 * a["win"] / a["n"],
        100 * 2 * a["merged"] / a["fills"] if a["fills"] else 0,
        a["adv"] / a["n"], 100 * a["wtilt"] / a["n"], a["pnl"] / a["n"]))


print("windows: %d\n" % len(WINS))
print("=== VALIDATION: passive under toxic model (does it flip to net-long-loser / -EV like live?) ===")
for bg in (1.0, 0.5, 0.3, 0.15):
    show("passive benign=%.2f" % bg, run(benign=bg, toxic=1.0))
for BG in (0.30, 0.15):
    print("\n=== benign=%.2f (adverse): does BREADTH (ladder) beat single-level? ===" % BG)
    show("1 level (best+tick)", run(benign=BG, levels=1))
    show("3 levels x2c", run(benign=BG, levels=3, spacing=0.02))
    show("6 levels x2c", run(benign=BG, levels=6, spacing=0.02))
    show("10 levels x3c", run(benign=BG, levels=10, spacing=0.03))
    show("6 levels + never-sell", run(benign=BG, levels=6, spacing=0.02, sell=False))
