"""Queue-grounded go/no-go: run OUR small deep-ladder policy through the REAL measured queue
(collected book snapshots) + real tape, per resolved window, and report our fill-rate-grounded
edge% vs the competitor's ground-truth edge%. Read-only.
Usage: python3 scripts/_book_edge.py <book_jsonl_path> [addr]"""
import sys
import json

from quoter.research.mm_book import load_snapshots, queue_fill, best_mid
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
    # PERSISTENT bid model: place the deep ladder ONCE (first snapshot with both bid sides),
    # queue-ahead from that snapshot, then consume the FULL remaining window flow — our bid
    # advances through the queue over the whole window. This is what queue_fill was built for;
    # re-placing every tick would either double-count (overlap) or reset our queue priority.
    place = next((s for s in snaps if s["yes"]["bids"] and s["no"]["bids"]), snaps[0])
    pts = place["ts"]
    ybids = place["yes"]["bids"]; nbids = place["no"]["bids"]
    ymid = best_mid(ybids, place["yes"]["asks"])   # true top-of-book mid (CLOB order-safe)
    inv = {"Up": 0.0, "Down": 0.0}; cost = {"Up": 0.0, "Down": 0.0}
    for q in deep_ladder_quotes(ymid, OUR_SIZE, LEVELS, STEP):
        bids = ybids if q.side == "Up" else nbids
        oi = 0 if q.side == "Up" else 1
        side_tape = [{"ts": t["ts"], "side": t["side"], "price": t["price"], "size": t["size"]}
                     for t in tape if t["oi"] == oi and t["ts"] >= pts]
        f = queue_fill(q.price, q.size, pts, bids, side_tape)
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
    loser = "Down" if winner == "Up" else "Up"
    matched = min(inv["Up"], inv["Down"])
    rows.append((slug, pnl, spent, inv[winner], inv[loser], matched))

print("windows simulated with real queue:", len(rows))
if rows:
    tot_pnl = sum(r[1] for r in rows); tot_spent = sum(r[2] for r in rows)
    edge = 100 * tot_pnl / tot_spent
    win_sh = sum(r[3] for r in rows); los_sh = sum(r[4] for r in rows); pair_sh = sum(r[5] for r in rows)
    print("OUR queue-grounded edge%%: total %+.2f%%  spent $%.2f  pnl $%+.2f" % (edge, tot_spent, tot_pnl))
    print("  $/day @ our deployed capital $%.0f/window: $%.2f" %
          (tot_spent / len(rows), (tot_spent / len(rows)) * (edge / 100) * WINDOWS_PER_DAY))
    print("  fill split: winner-side %.0f sh vs loser-side %.0f sh (%.0f%% on losers); matched pairs %.0f sh" %
          (win_sh, los_sh, 100 * los_sh / (win_sh + los_sh) if (win_sh + los_sh) else 0, pair_sh))
    print("  => %.0f%% of our fills were on the LOSING side (adverse selection signal)" %
          (100 * los_sh / (win_sh + los_sh) if (win_sh + los_sh) else 0))

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
