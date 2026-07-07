"""Window-level head-to-head: 0xb27b's REALIZED per-window metrics (from the competitor
activity collector) vs OUR passive top-of-book backtest, on the SAME resolved windows.
His side: buy$ / merged / redeemed / net PnL / edge(=pnl/buy) / match% from /activity.
Our side: passive maker backtest (reuses _chase.py's fill model) -> edge / adverse / match%.
Read-only. Run on server with POLY_MM_CACHE=/home/ubuntu/cache_poly_mm.
Usage: python3 scripts/_benchmark.py <competitor_jsonl> <book_jsonl>"""
import sys
import json
import collections

from quoter.research.mm_tape import load_window

COMP = sys.argv[1]
BOOK = sys.argv[2]
TICK = 0.001

# --- his side: group activity by window ---
comp = collections.defaultdict(list)
for l in open(COMP):
    l = l.strip()
    if not l:
        continue
    try:
        r = json.loads(l)
    except json.JSONDecodeError:
        continue
    s = r.get("slug", "")
    if isinstance(s, str) and s.startswith("btc-updown-5m-"):
        comp[s].append(r)
comp_windows = set(comp)


def his_stats(evs):
    buy = 0.0
    up = 0.0
    dn = 0.0
    merged = 0.0          # pairs
    ret = 0.0             # $ back from merges + redeems + sells
    for e in evs:
        t = e.get("type")
        sz = float(e.get("size", 0) or 0)
        u = float(e.get("usdcSize", 0) or 0)
        if t == "TRADE" and e.get("side") == "BUY":
            buy += u
            if e.get("outcome") == "Up":
                up += sz
            else:
                dn += sz
        elif t == "TRADE" and e.get("side") == "SELL":
            ret += u
        elif t == "MERGE":
            merged += sz
            ret += u
        elif t == "REDEEM":
            ret += u
    bought = up + dn
    pnl = ret - buy
    return {"buy": buy, "merged": merged, "pnl": pnl, "bought": bought,
            "match": (2 * merged / bought) if bought else 0.0,
            "edge": (pnl / buy) if buy > 0 else None}


# --- book by window (stream-filter to only the competitor's windows: memory-safe) ---
byslug = collections.defaultdict(list)
for l in open(BOOK):
    l = l.strip()
    if not l:
        continue
    try:
        r = json.loads(l)
    except json.JSONDecodeError:
        continue
    if r.get("slug") in comp_windows:
        byslug[r["slug"]].append(r)
for s in byslug.values():
    s.sort(key=lambda x: x["ts"])


def our_stats(snaps, tape, winner):
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
            if inv[side] - inv[other] >= 10:
                continue
            bb = max(float(p) for p, _ in bids)
            ba = min((float(p) for p, _ in asks), default=1.0)
            our = round(bb + TICK, 3)
            if our >= ba or our >= 0.99:
                continue
            v = sum(t["size"] for t in st[oi]
                    if ts <= t["ts"] < end and t["side"] == "SELL" and t["price"] <= our)
            f = min(5.0, v)
            if f > 0:
                inv[side] += f
                spent += f * our
                fills += f
        m = min(inv["Up"], inv["Down"])
        if m > 0:
            returned += m
            merged += m
            inv["Up"] -= m
            inv["Down"] -= m
    if spent <= 0:
        return None
    returned += inv[winner]
    loser = "Down" if winner == "Up" else "Up"
    return {"spent": spent, "pnl": returned - spent, "edge": (returned - spent) / spent,
            "adverse": inv[loser], "match": (2 * merged / fills) if fills else 0.0}


overlap = sorted(comp_windows & set(byslug))
rows = []
for slug in overlap:
    w = load_window(slug)
    if not w or not w[0] or w[1] not in ("Up", "Down"):
        continue
    tape, winner, _ = w
    o = our_stats(byslug[slug], tape, winner)
    if o is None:
        continue
    rows.append((slug, winner, his_stats(comp[slug]), o))

print("overlapping RESOLVED windows: %d\n" % len(rows))
print("%-12s win  |  HIS buy$  merg  pnl$   edge  match | OUR edge  adv  match" % "window")
Hbuy = Hpnl = 0.0
Oedge = []
Oadv = 0.0
Hm = []
Om = []
for slug, winner, h, o in rows:
    print("%-12s %-4s | %7.0f %5.0f %+6.1f %+5.1f%% %3.0f%% | %+5.2f%% %4.1f %3.0f%%" % (
        slug[-10:], winner, h["buy"], h["merged"], h["pnl"],
        100 * (h["edge"] or 0), 100 * h["match"],
        100 * o["edge"], o["adverse"], 100 * o["match"]))
    Hbuy += h["buy"]
    Hpnl += h["pnl"]
    Hm.append(h["match"])
    Oedge.append(o["edge"])
    Oadv += o["adverse"]
    Om.append(o["match"])
n = len(rows)
if n:
    print("\nAGGREGATE (%d resolved overlapping windows):" % n)
    print("  HIS (realized): buy $%.0f  net pnl $%+.1f  EDGE %+.2f%%  avg match %.0f%%" % (
        Hbuy, Hpnl, 100 * Hpnl / Hbuy if Hbuy else 0, 100 * sum(Hm) / n))
    print("  OUR (passive backtest): EDGE %+.2f%%  avg adverse %.1f sh/win  avg match %.0f%%" % (
        100 * sum(Oedge) / n, Oadv / n, 100 * sum(Om) / n))
