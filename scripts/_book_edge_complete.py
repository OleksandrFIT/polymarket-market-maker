"""Does pair-completion flip our queue-grounded edge positive? On the collected book+tape data,
compare BASELINE (no completion) vs NEAR-END vs CONTINUOUS taker-completion: matched-pair
fraction, edge%, win-rate. Read-only, no trading.
Usage: python3 scripts/_book_edge_complete.py <book_jsonl> [threshold]"""
import sys
import json
import statistics

from quoter.research.mm_book import load_snapshots, queue_fill, best_mid, best_ask
from quoter.research.mm_policy import deep_ladder_quotes
from quoter.research.mm_tape import load_window, subgraph_targets
from quoter.research.mm_calibrate import realized_pnl
from quoter.research.mm_complete import completion_buy

BOOK = sys.argv[1] if len(sys.argv) > 1 else "data/book.jsonl"
THRESHOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
ADDR = "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
SIZE, LEVELS, STEP = 5, 15, 0.03
WINDOWS_PER_DAY = 288


def _result(inv, spent, winner):
    matched = min(inv["Up"], inv["Down"])
    total = inv["Up"] + inv["Down"]
    return {"pnl": inv[winner] - spent, "spent": spent, "matched": matched, "total": total}


def simulate(slug):
    w = load_window(slug)
    if not w or not w[0]:
        return None
    tape, winner, _ = w
    snaps = load_snapshots(BOOK, slug)
    if not snaps:
        return None
    place = next((s for s in snaps if s["yes"]["bids"] and s["no"]["bids"]), snaps[0])
    pts = place["ts"]
    pbids = {"Up": place["yes"]["bids"], "Down": place["no"]["bids"]}
    ymid = best_mid(place["yes"]["bids"], place["yes"]["asks"])
    quotes = deep_ladder_quotes(ymid, SIZE, LEVELS, STEP)
    side_tape = {"Up": [t for t in tape if t["oi"] == 0 and t["ts"] >= pts],
                 "Down": [t for t in tape if t["oi"] == 1 and t["ts"] >= pts]}
    close_ts = int(slug.rsplit("-", 1)[1]) + 300

    def maker_fills(upto):
        inv = {"Up": 0.0, "Down": 0.0}; cost = {"Up": 0.0, "Down": 0.0}
        for q in quotes:
            st = [t for t in side_tape[q.side] if t["ts"] < upto]
            f = queue_fill(q.price, q.size, pts, pbids[q.side], st)
            if f > 0:
                inv[q.side] += f; cost[q.side] += f * q.price
        return inv, cost

    def ask_at(snap, side):
        return best_ask(snap["yes"]["asks"] if side == "Up" else snap["no"]["asks"])

    # --- baseline: full-window maker fills, hold ---
    inv, cost = maker_fills(close_ts + 1)
    base = _result(inv, cost["Up"] + cost["Down"], winner)

    # --- near-end: complete accumulated naked at the LAST snapshot's ask ---
    ni = dict(inv); nc = dict(cost)
    heavy = "Up" if ni["Up"] > ni["Down"] else "Down"
    light = "Down" if heavy == "Up" else "Up"
    naked = abs(ni["Up"] - ni["Down"])
    havg = nc[heavy] / ni[heavy] if ni[heavy] > 0 else 0.0
    lask = ask_at(snaps[-1], light)
    if lask is not None:
        buy = completion_buy(heavy, havg, lask, naked, THRESHOLD)
        if buy:
            ni[buy[0]] += buy[1]; nc[buy[0]] += buy[1] * buy[2]
    near = _result(ni, nc["Up"] + nc["Down"], winner)

    # --- continuous: complete as naked accrues, snapshot by snapshot ---
    completed = {"Up": 0.0, "Down": 0.0}; taker_cost = 0.0
    for s in snaps:
        mi, mc = maker_fills(s["ts"] + 1)
        cur = {k: mi[k] + completed[k] for k in ("Up", "Down")}
        h = "Up" if cur["Up"] > cur["Down"] else "Down"
        lt = "Down" if h == "Up" else "Up"
        nk = abs(cur["Up"] - cur["Down"])
        ha = mc[h] / mi[h] if mi[h] > 0 else 0.0
        la = ask_at(s, lt)
        if nk > 0 and la is not None:
            buy = completion_buy(h, ha, la, nk, THRESHOLD)
            if buy:
                completed[buy[0]] += buy[1]; taker_cost += buy[1] * buy[2]
    mi, mc = maker_fills(close_ts + 1)
    ci = {k: mi[k] + completed[k] for k in ("Up", "Down")}
    cont = _result(ci, mc["Up"] + mc["Down"] + taker_cost, winner)

    return {"base": base, "near": near, "cont": cont}


def agg(rows, key):
    rs = [r[key] for r in rows]
    spent = sum(r["spent"] for r in rs)
    pnl = sum(r["pnl"] for r in rs)
    matched = sum(r["matched"] for r in rs)
    total = sum(r["total"] for r in rs)
    wins = sum(1 for r in rs if r["pnl"] > 0)
    edge = 100 * pnl / spent if spent else 0.0
    mfrac = 100 * 2 * matched / total if total else 0.0
    return edge, mfrac, 100 * wins / len(rs) if rs else 0.0, spent, pnl


slugs = set()
with open(BOOK) as f:
    for line in f:
        try:
            slugs.add(json.loads(line)["slug"])
        except Exception:
            continue
print("windows with book data:", len(slugs), " threshold:", THRESHOLD)

rows = [r for r in (simulate(s) for s in sorted(slugs)) if r]
print("windows simulated:", len(rows))
if rows:
    print("\n%-10s %10s %12s %10s" % ("mode", "edge%", "matched%", "win%"))
    for key, name in (("base", "baseline"), ("near", "near-end"), ("cont", "continuous")):
        edge, mfrac, winr, spent, pnl = agg(rows, key)
        dpw = spent / len(rows)
        print("%-10s %+9.2f%% %11.0f%% %9.0f%%   spent $%.0f pnl $%+.0f  $/day@$%.0f=%+.0f" %
              (name, edge, mfrac, winr, spent, pnl, dpw, dpw * (edge / 100) * WINDOWS_PER_DAY))

# competitor ground-truth context
tg = subgraph_targets(ADDR, max_pages=20)
comp = [realized_pnl(t, load_window(t["slug"])[1]) for t in tg if load_window(t["slug"])]
if comp:
    print("\ncompetitor ground-truth pnl over %d windows: $%+.2f" % (len(comp), sum(comp)))

print("\nCAVEATS: taker-completion pays the spread (may not fully lock); light_ask from snapshot")
print("is a proxy; small sample/regime; approach-A queue (no intra-tick refill).")
