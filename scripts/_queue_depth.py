"""Realistic queue-position gauge. How much resting depth already sits AHEAD of our deep
ladder at each minute of the window — the thing that decides whether we're near the front
(-4% edge) or the back (-42%) of the queue. If the deep book is blanketed within seconds of
open, a small player is structurally back-of-queue regardless of latency. Read-only.
Usage: python3 scripts/_queue_depth.py <book_jsonl>"""
import sys
import json
import collections
import statistics

from quoter.research.mm_book import best_mid, depth_ahead

BOOK = sys.argv[1] if len(sys.argv) > 1 else "data/book.jsonl"
LEVELS, STEP = 15, 0.03      # our deep ladder: 15 levels, 0.03 apart, below each side's price

rows = [json.loads(l) for l in open(BOOK) if l.strip()]
byw = collections.defaultdict(list)
for r in rows:
    byw[r["slug"]].append(r)

buckets = collections.defaultdict(list)     # minute -> total depth ahead of our full ladder
deep_tail = collections.defaultdict(list)   # minute -> resting depth at price<0.35 (both sides)
for slug, ws in byw.items():
    open_ts = int(slug.rsplit("-", 1)[1])
    for s in ws:
        minute = int((s["ts"] - open_ts) // 60)
        if minute < 0 or minute > 4:
            continue
        yb, nb = s["yes"]["bids"], s["no"]["bids"]
        if not yb or not nb:
            continue
        ymid = best_mid(yb, s["yes"]["asks"])
        ahead = 0.0
        for k in range(LEVELS):
            pu = round(ymid - STEP * (k + 1), 3)
            pn = round((1 - ymid) - STEP * (k + 1), 3)
            if pu > 0:
                ahead += depth_ahead(yb, pu)
            if pn > 0:
                ahead += depth_ahead(nb, pn)
        buckets[minute].append(ahead)
        deep_tail[minute].append(sum(sz for p, sz in yb if p < 0.35)
                                 + sum(sz for p, sz in nb if p < 0.35))

print("windows:", len(byw))
print("\nminute | median depth AHEAD of our %d-level ladder | median deep-tail(<0.35)" % LEVELS)
for m in sorted(buckets):
    print("  min %d :  ahead %9.0f sh   deep-tail %9.0f sh   (n=%d snaps)" %
          (m, statistics.median(buckets[m]), statistics.median(deep_tail[m]), len(buckets[m])))

first = []
for slug, ws in byw.items():
    ws = sorted(ws, key=lambda x: x["ts"])
    s = ws[0]
    if not s["yes"]["bids"] or not s["no"]["bids"]:
        continue
    open_ts = int(slug.rsplit("-", 1)[1])
    ymid = best_mid(s["yes"]["bids"], s["yes"]["asks"])
    a = sum(depth_ahead(s["yes"]["bids"], round(ymid - STEP * (k + 1), 3))
            for k in range(LEVELS) if ymid - STEP * (k + 1) > 0)
    first.append((s["ts"] - open_ts, a))
if first:
    print("\nfirst-observed snapshot/window: median %.0fs into window, median Up-ladder depth-ahead %.0f sh"
          % (statistics.median([f[0] for f in first]), statistics.median([f[1] for f in first])))
print("\nREAD: huge depth-ahead already at min 0 => book blanketed within seconds => small player")
print("is realistically BACK-of-queue (~-42%), NOT front (~-4%). Latency (ca-central-1) can't fix a")
print("capital-blanketed queue. Thinning depth-ahead over time would mean early placement helps.")
