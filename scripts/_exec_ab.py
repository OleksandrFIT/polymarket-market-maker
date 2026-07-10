"""Execution A/B on the SAME recorded book tape: top_book (maker, shadow-fill, OPTIMISTIC) vs
momentum (taker, decision-grade). Per-window pair_cost/match_naked/PnL for each -> side-by-side.
FIDELITY: momentum = aggressor, real; top_book = shadow-fill UPPER BOUND (offline cannot model
adverse selection). This is a cheap preview of the LIVE topbook_fillquality measurement, NOT a
substitute. Run: POLY_MM_CACHE=/home/ubuntu/cache_poly_mm python3 scripts/_exec_ab.py <book_jsonl>"""
import sys
import json
import collections

from quoter.research.mm_tape import load_window
from quoter.research.pairquality import summarize
from quoter.research.exec_ab import top_book_window, momentum_window, hybrid_window

BOOK = sys.argv[1] if len(sys.argv) > 1 else "/tmp/book_all.jsonl"


def _load(path):
    byslug = collections.defaultdict(list)
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        byslug[r["slug"]].append(r)
    for s in byslug.values():
        s.sort(key=lambda x: x["ts"])
    return byslug


def _row(name, recs):
    s = summarize(recs)
    pnl = [r["pnl"] for r in recs]
    return "%-34s pair mean %s med %s | %%<$1 %s | match:naked %s | PnL/win $%+.3f | %s" % (
        name,
        "%.4f" % s["mean_pair_cost"] if s["mean_pair_cost"] is not None else "n/a",
        "%.4f" % s["median_pair_cost"] if s["median_pair_cost"] is not None else "n/a",
        "%3.0f%%" % (100 * s["pct_sub_dollar"]) if s["pct_sub_dollar"] is not None else "n/a",
        "%.1f" % s["median_match_naked"] if s["median_match_naked"] is not None else "n/a",
        (sum(pnl) / len(pnl)) if pnl else 0.0,
        s["outcomes"])


def main():
    byslug = _load(BOOK)
    tb, mo, hy = [], [], []
    for slug, snaps in byslug.items():
        w = load_window(slug)
        if not w or not w[0]:
            continue
        _tape, winner, _ = w
        tb.append(top_book_window(snaps, _tape, winner, slug))
        mo.append(momentum_window(snaps, _tape, winner, slug))
        hy.append(hybrid_window(snaps, _tape, winner, slug))
    if not tb:
        print("no resolved windows in tape (need network/cache to resolve winners via load_window)")
        return
    print("windows: %d (same tape, same resolved winners)\n" % len(tb))
    print("FIDELITY: momentum/hybrid taker legs = decision-grade | maker legs = OPTIMISTIC shadow-fill")
    print("          (offline can't model adverse selection on the maker fills)\n")
    print(_row("top_book (maker, OPTIMISTIC)", tb))
    print(_row("momentum (taker, real)", mo))
    print(_row("hybrid (0xb27b 53/47 replica)", hy))


if __name__ == "__main__":
    main()
