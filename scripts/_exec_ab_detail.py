"""Detailed decomposition of both execution styles on the same tape: arb vs naked component,
naked mean/stdev (zero-mean vs negative-mean), win/loss magnitudes, full PnL distribution.
Throwaway analysis (not committed to prod). Run: POLY_MM_CACHE=... python3 scripts/_exec_ab_detail.py <book_jsonl>"""
import sys
import json
import collections
import statistics as st

from quoter.research.mm_tape import load_window
from quoter.research.exec_ab import top_book_window, momentum_window, hybrid_window

BOOK = sys.argv[1] if len(sys.argv) > 1 else "/tmp/book_slice.jsonl"


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


def q(xs, p):
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))]


def analyze(name, recs):
    n = len(recs)
    pnl = [r["pnl"] for r in recs]
    priced = [r["pair_cost"] for r in recs if r["pair_cost"] is not None]
    mn = [r["match_naked"] for r in recs if r["match_naked"] is not None]
    # decomposition: arb = merged*(1-pair_cost); naked = pnl - arb
    arb = [r["pairs_merged"] * (1 - r["pair_cost"]) if r["pair_cost"] is not None else 0.0
           for r in recs]
    naked = [pnl[i] - arb[i] for i in range(n)]
    won = [naked[i] for i in range(n) if recs[i]["resid_outcome"] == "WON"]
    lost = [naked[i] for i in range(n) if recs[i]["resid_outcome"] == "LOST"]
    flat = [naked[i] for i in range(n) if recs[i]["resid_outcome"] == "flat"]
    print("=== %s (n=%d) ===" % (name, n))
    print("  pair_cost: mean %.4f median %.4f | %%<$1 %.0f%%" % (
        sum(priced) / len(priced), st.median(priced), 100 * sum(1 for x in priced if x < 1) / len(priced)))
    print("  match:naked median %.1f" % (st.median(mn) if mn else 0))
    print("  PnL/win: mean $%+.3f | median $%+.3f | stdev $%.2f | min $%+.2f | max $%+.2f | total $%+.2f" % (
        sum(pnl) / n, st.median(pnl), st.pstdev(pnl), min(pnl), max(pnl), sum(pnl)))
    print("  DECOMP: arb $%+.2f/win (total $%+.2f) | naked $%+.3f/win (total $%+.2f)" % (
        sum(arb) / n, sum(arb), sum(naked) / n, sum(naked)))
    print("  NAKED mean $%+.3f | stdev $%.2f  -> %s" % (
        sum(naked) / n, st.pstdev(naked),
        "ZERO-mean-ish" if abs(sum(naked) / n) < st.pstdev(naked) / (n ** 0.5) else "NEGATIVE-mean (biased loser)" if sum(naked) / n < 0 else "positive-mean"))
    print("  outcomes: WON %d (naked avg $%+.2f) | LOST %d (naked avg $%+.2f) | flat %d" % (
        len(won), sum(won) / len(won) if won else 0,
        len(lost), sum(lost) / len(lost) if lost else 0, len(flat)))
    print("  PnL pctiles: p10 $%+.2f | p25 $%+.2f | p50 $%+.2f | p75 $%+.2f | p90 $%+.2f" % (
        q(pnl, .1), q(pnl, .25), q(pnl, .5), q(pnl, .75), q(pnl, .9)))
    print()


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
        print("no resolved windows")
        return
    analyze("top_book (maker, OPTIMISTIC shadow-fill)", tb)
    analyze("momentum (taker, REAL decision-grade)", mo)
    analyze("hybrid (0xb27b 53/47 replica)", hy)


if __name__ == "__main__":
    main()
