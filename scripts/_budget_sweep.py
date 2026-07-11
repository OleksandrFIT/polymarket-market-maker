"""Project hybrid PnL at different per-window budgets on the SAME recorded windows. Shows how
arb (scales with budget) vs naked drain (capped by resid_cap) trade off as the budget rises.
OPTIMISTIC (offline shadow-fill maker). Run: POLY_MM_CACHE=... python3 scripts/_budget_sweep.py <book_jsonl> [csv-of-pwc]"""
import sys
import json
import collections
import statistics as st

from quoter.research.mm_tape import load_window
from quoter.research.exec_ab import hybrid_window

BOOK = sys.argv[1] if len(sys.argv) > 1 else "/tmp/book_slice.jsonl"
PWCS = [float(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else [15, 30, 50, 100]
LB = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0        # chase lookback (early=10)
TH = float(sys.argv[4]) if len(sys.argv) > 4 else 0.03        # chase threshold (early=0.01)

byslug = collections.defaultdict(list)
for line in open(BOOK):
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

resolved = []
for slug, snaps in byslug.items():
    w = load_window(slug)
    if not w or not w[0]:
        continue
    resolved.append((slug, snaps, w[0], w[1]))

print("windows: %d | chase lookback=%.0fs threshold=%.3f (OPTIMISTIC shadow-fill)\n" % (
    len(resolved), LB, TH))
print("%-8s | PnL/win | total   | naked-WR   | match:naked | pair  | arb/win | naked/win" % "budget")
for pwc in PWCS:
    recs = [hybrid_window(snaps, tape, winner, slug, pwc=pwc, lookback=LB, threshold=TH)
            for slug, snaps, tape, winner in resolved]
    n = len(recs)
    pnl = [r["pnl"] for r in recs]
    won = sum(1 for r in recs if r["resid_outcome"] == "WON")
    lost = sum(1 for r in recs if r["resid_outcome"] == "LOST")
    mn = [r["match_naked"] for r in recs if r["match_naked"] is not None]
    pcs = [r["pair_cost"] for r in recs if r["pair_cost"] is not None]
    arb = [r["pairs_merged"] * (1 - r["pair_cost"]) if r["pair_cost"] is not None else 0.0 for r in recs]
    naked = [pnl[i] - arb[i] for i in range(n)]
    print("$%-7.0f | $%+.3f | $%+7.1f | %3.0f%% (W%d/L%d) | %5.1f | %.3f | $%+.3f | $%+.3f" % (
        pwc, sum(pnl) / n, sum(pnl), 100 * won / max(won + lost, 1), won, lost,
        st.median(mn) if mn else 0, sum(pcs) / len(pcs) if pcs else 0,
        sum(arb) / n, sum(naked) / n))
