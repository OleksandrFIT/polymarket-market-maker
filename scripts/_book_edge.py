"""Queue-grounded go/no-go: run OUR small deep-ladder policy through the REAL measured queue
(collected book snapshots) + real tape, per resolved window, and report our fill-rate-grounded
edge% vs the competitor's ground-truth edge%. Read-only.
Usage: python3 scripts/_book_edge.py <book_jsonl_path> [addr]"""
import sys
import json

from quoter.research.mm_book import load_snapshots, queue_fill
from quoter.research.mm_policy import deep_ladder_quotes
from quoter.research.mm_tape import load_window, subgraph_targets
from quoter.research.mm_calibrate import realized_pnl

BOOK = sys.argv[1] if len(sys.argv) > 1 else "data/book.jsonl"
ADDR = sys.argv[2] if len(sys.argv) > 2 else "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
OUR_SIZE = 5          # shares per bid (our scale)
LEVELS = 15
STEP = 0.03
WINDOWS_PER_DAY = 288

# distinct slugs present in the collected book file
import json
slugs = set()
with open(BOOK) as f:
    for line in f:
        try:
            slugs.add(json.loads(line)["slug"])
        except Exception:
            continue
print("windows with collected book data:", len(slugs))

rows = []   # (slug, our_pnl, our_spent)
for slug in sorted(slugs):
    w = load_window(slug)
    if not w or not w[0]:
        continue
    tape, winner, _open_ts = w
    snaps = load_snapshots(BOOK, slug)
    if not snaps:
        continue
    inv = {"Up": 0.0, "Down": 0.0}; cost = {"Up": 0.0, "Down": 0.0}
    for snap in snaps:                       # each snapshot = one quote-refresh tick
        ts = snap["ts"]; ybids = snap["yes"]["bids"]; nbids = snap["no"]["bids"]
        ymid = ybids[0][0] if ybids else 0.5   # best bid as mid proxy
        for q in deep_ladder_quotes(ymid, OUR_SIZE, LEVELS, STEP):
            bids = ybids if q.side == "Up" else nbids
            oi = 0 if q.side == "Up" else 1
            side_tape = [{"ts": t["ts"], "side": t["side"], "price": t["price"], "size": t["size"]}
                         for t in tape if t["oi"] == oi and t["ts"] >= ts]
            f = queue_fill(q.price, q.size, ts, bids, side_tape)
            if f > 0:
                inv[q.side] += f; cost[q.side] += f * q.price
    spent = cost["Up"] + cost["Down"]
    if spent <= 0:
        continue
    # Held to resolution: a merged pair ($1) and holding both legs (winner $1 + loser $0)
    # give the SAME $1 per matched pair, so merging doesn't change resolution PnL —
    # payout is simply the winning-side inventory; the loser expires worthless.
    returned = inv[winner]
    pnl = returned - spent
    rows.append((slug, pnl, spent))

print("windows simulated with real queue:", len(rows))
if rows:
    tot_pnl = sum(r[1] for r in rows); tot_spent = sum(r[2] for r in rows)
    edge = 100 * tot_pnl / tot_spent
    print("OUR queue-grounded edge%%: total %+.2f%%  spent $%.2f  pnl $%+.2f" % (edge, tot_spent, tot_pnl))
    print("  $/day @ our deployed capital $%.0f/window: $%.2f" %
          (tot_spent / len(rows), (tot_spent / len(rows)) * (edge / 100) * WINDOWS_PER_DAY))

# competitor ground-truth for the same/nearby windows (context)
tg = subgraph_targets(ADDR, max_pages=20)
comp = []
for t in tg:
    w = load_window(t["slug"])
    if w:
        comp.append(realized_pnl(t, w[1]))
if comp:
    print("competitor ground-truth pnl over %d windows: $%+.2f" % (len(comp), sum(comp)))

print("\nCAVEATS: fill needs BOTH sides (naked risk if one-sided); snapshot=tick is a proxy;")
print("small sample/regime; queue_fill ignores intra-tick queue refill (approach A).")
