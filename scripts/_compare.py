"""Daily COMPETITOR vs US comparison, one command. Prints the competitor's real-fill profile
(static April subgraph sample) next to OUR queue-grounded profile on the collected book+tape
data, bracketed by queue assumption (back / front / front+completion). Read-only, no trading.
Usage: python3 scripts/_compare.py <book_jsonl> [addr]"""
import sys
import json
import statistics

from quoter.research.mm_book import load_snapshots, queue_fill, best_mid, best_ask
from quoter.research.mm_policy import deep_ladder_quotes
from quoter.research.mm_tape import load_window, subgraph_targets
from quoter.research.mm_calibrate import realized_pnl
from quoter.research.mm_complete import completion_buy

BOOK = sys.argv[1] if len(sys.argv) > 1 else "data/book.jsonl"
ADDR = sys.argv[2] if len(sys.argv) > 2 else "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
SIZE, LEVELS, STEP = 5, 15, 0.03
THRESHOLD = 1.0


def competitor():
    rows = []
    for t in subgraph_targets(ADDR, max_pages=20):
        w = load_window(t["slug"])
        if not w:
            continue
        winner = w[1]
        su, sd, au, ad = t["size_up"], t["size_dn"], t["avg_up"], t["avg_dn"]
        if su + sd <= 0:
            continue
        rows.append({
            "pnl": realized_pnl(t, winner), "spent": su * au + sd * ad,
            "matched": 2 * min(su, sd) / (su + sd),
            "win_avg": au if winner == "Up" else ad,
            "los_avg": ad if winner == "Up" else au,
            "size": (su + sd) / 2,
        })
    return rows


def us(front, complete):
    slugs = set()
    with open(BOOK) as f:
        for line in f:
            try:
                slugs.add(json.loads(line)["slug"])
            except Exception:
                continue
    rows = []
    for slug in sorted(slugs):
        w = load_window(slug)
        if not w or not w[0]:
            continue
        tape, winner, _ = w
        snaps = load_snapshots(BOOK, slug)
        if not snaps:
            continue
        place = next((s for s in snaps if s["yes"]["bids"] and s["no"]["bids"]), snaps[0])
        pts = place["ts"]
        pbids = {"Up": place["yes"]["bids"], "Down": place["no"]["bids"]}
        ymid = best_mid(place["yes"]["bids"], place["yes"]["asks"])
        side_tape = {"Up": [t for t in tape if t["oi"] == 0 and t["ts"] >= pts],
                     "Down": [t for t in tape if t["oi"] == 1 and t["ts"] >= pts]}
        inv = {"Up": 0.0, "Down": 0.0}; cost = {"Up": 0.0, "Down": 0.0}
        for q in deep_ladder_quotes(ymid, SIZE, LEVELS, STEP):
            bids = [] if front else pbids[q.side]     # [] -> depth_ahead=0 -> front of queue
            f = queue_fill(q.price, q.size, pts, bids, side_tape[q.side])
            if f > 0:
                inv[q.side] += f; cost[q.side] += f * q.price
        spent = cost["Up"] + cost["Down"]
        if complete:
            heavy = "Up" if inv["Up"] > inv["Down"] else "Down"
            light = "Down" if heavy == "Up" else "Up"
            naked = abs(inv["Up"] - inv["Down"])
            havg = cost[heavy] / inv[heavy] if inv[heavy] > 0 else 0.0
            lask = best_ask(snaps[-1]["yes"]["asks"] if light == "Up" else snaps[-1]["no"]["asks"])
            if lask is not None:
                buy = completion_buy(heavy, havg, lask, naked, THRESHOLD)
                if buy:
                    inv[buy[0]] += buy[1]; cost[buy[0]] += buy[1] * buy[2]; spent += buy[1] * buy[2]
        if spent <= 0:
            continue
        loser = "Down" if winner == "Up" else "Up"
        rows.append({"pnl": inv[winner] - spent, "spent": spent,
                     "matched": min(inv["Up"], inv["Down"]), "total": inv["Up"] + inv["Down"],
                     "win_sh": inv[winner], "los_sh": inv[loser]})
    return rows


def agg_us(rows):
    if not rows:
        return None
    sp = sum(r["spent"] for r in rows); pn = sum(r["pnl"] for r in rows)
    ms = sum(r["matched"] for r in rows); tot = sum(r["total"] for r in rows)
    ws = sum(r["win_sh"] for r in rows); ls = sum(r["los_sh"] for r in rows)
    return {"edge": 100 * pn / sp if sp else 0.0, "matched": 100 * 2 * ms / tot if tot else 0.0,
            "win": 100 * sum(1 for r in rows if r["pnl"] > 0) / len(rows),
            "loser_frac": 100 * ls / (ws + ls) if ws + ls else 0.0, "n": len(rows)}


print("=" * 68)
print("COMPETITOR vs US  —  daily comparison")
print("=" * 68)

c = competitor()
if c:
    cs = sum(r["spent"] for r in c); cp = sum(r["pnl"] for r in c)
    print("\nCOMPETITOR 0xb27b (%d real-fill windows, static April sample):" % len(c))
    print("  edge %+.2f%%   matched %.0f%%   win %.0f%%   entry win/los %.2f/%.2f   size ~%.0f/side" % (
        100 * cp / cs, 100 * statistics.mean(r["matched"] for r in c),
        100 * sum(1 for r in c if r["pnl"] > 0) / len(c),
        statistics.mean(r["win_avg"] for r in c), statistics.mean(r["los_avg"] for r in c),
        statistics.median([r["size"] for r in c])))

print("\nUS (queue-grounded on collected book data, size %d/level):" % SIZE)
print("  %-22s %9s %9s %6s %11s" % ("queue assumption", "edge%", "matched%", "win%", "loser-fill%"))
for front, comp, name in ((False, False, "back-of-queue"), (True, False, "front-of-queue"),
                          (True, True, "front + completion")):
    a = agg_us(us(front, comp))
    if a:
        print("  %-22s %+8.2f%% %8.0f%% %5.0f%% %10.0f%%   (n=%d)" %
              (name, a["edge"], a["matched"], a["win"], a["loser_frac"], a["n"]))

print("\nLEVER: queue position (time-priority), NOT latency (ca-central-1 is best legal).")
print("back->front moves edge sharply; realistic position is between. Controllable levers:")
print("early/continuous resting + completion. Competitor's edge = book-blanketing scale/priority.")
