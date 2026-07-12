"""Test the maker-both-sides thesis SPLIT BY REGIME (chop vs reversal vs trend): does resting bids
on both sides assemble cheap pairs in CHOP (both sides get dumped in turn), or leave a naked loser
in TREND (only the losing side ever fills)? Runs the real top_book_window (maker shadow-fill +
merge + complete) on each window, classified by its Up-price path. Streams book jsonl.
Run: POLY_MM_CACHE=... python3 scripts/_chop_maker_sim.py <book_jsonl> [more...]"""
import sys
import json
import collections
import statistics as st

from quoter.research.mm_tape import load_window
from quoter.research.exec_ab import top_book_window, hybrid_window, _mid

GRID = list(range(0, 301, 20))


def resample(pts):
    if len(pts) < 3:
        return None
    out, j = [], 0
    for g in GRID:
        while j + 1 < len(pts) and pts[j + 1][0] <= g:
            j += 1
        if g <= pts[0][0]:
            out.append(pts[0][1])
        elif g >= pts[-1][0]:
            out.append(pts[-1][1])
        else:
            (t0, m0), (t1, m1) = pts[j], pts[min(j + 1, len(pts) - 1)]
            out.append(m0 if t1 == t0 else m0 + (m1 - m0) * (g - t0) / (t1 - t0))
    return out


def regime(u):
    sgn = [1 if x >= 0.5 else -1 for x in u]
    crosses = sum(1 for i in range(len(sgn) - 1) if sgn[i] != sgn[i + 1])
    if crosses >= 2:
        return "chop"
    if crosses == 1:
        return "reversal"
    return "trend"


def main():
    per = collections.defaultdict(list)          # slug -> [(rel_t, up_mid)]
    snapd = collections.defaultdict(list)         # slug -> [snap dicts]
    for path in sys.argv[1:]:
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            sl = r.get("slug", "")
            if not sl.startswith("btc-updown-5m-"):
                continue
            ts0 = int(sl.rsplit("-", 1)[1])
            m = _mid(r.get("yes", {}))
            if m is not None:
                per[ts0].append((r["ts"] - ts0, m))
            snapd[sl].append(r)

    def mk():
        return collections.defaultdict(lambda: {"n": 0, "pair": [], "pnl": [], "naked_n": 0,
                                                "naked_lost": 0, "merged": []})
    aggM, aggH = mk(), mk()               # maker-only (top_book) vs hybrid (taker winner + maker loser)
    for slug, snaps in snapd.items():
        ts0 = int(slug.rsplit("-", 1)[1])
        u = resample(sorted(per[ts0]))
        if u is None:
            continue
        w = load_window(slug)
        if not w or not w[0]:
            continue
        tape, winner, _ = w
        snaps.sort(key=lambda x: x["ts"])
        reg = regime(u)
        # aggM = PURE maker (gate_sec=0 -> no taker completion); aggH = maker + taker-completion
        for rec, agg in ((top_book_window(snaps, tape, winner, slug, gate_sec=0.0), aggM),
                         (top_book_window(snaps, tape, winner, slug, gate_sec=45.0), aggH)):
            a = agg[reg]
            a["n"] += 1
            a["pnl"].append(rec["pnl"])
            a["merged"].append(rec["pairs_merged"])
            if rec["pair_cost"] is not None:
                a["pair"].append(rec["pair_cost"])
            if rec["resid_outcome"] != "flat":
                a["naked_n"] += 1
                if rec["resid_outcome"] == "LOST":
                    a["naked_lost"] += 1

    def show(name, agg):
        tot = sum(a["n"] for a in agg.values())
        print("=== %s (%d windows, by regime) ===" % (name, tot))
        print("%-9s | n   | pair  | merged/w | naked-LOST%% | PnL/w   | total" % "regime")
        for reg in ("chop", "reversal", "trend"):
            a = agg.get(reg)
            if not a or not a["n"]:
                continue
            n = a["n"]
            print("%-9s | %3d | %.3f | %6.1f   | %6.0f%%      | $%+.3f | $%+.1f" % (
                reg, n, st.mean(a["pair"]) if a["pair"] else 0, st.mean(a["merged"]),
                100 * a["naked_lost"] / max(a["naked_n"], 1), st.mean(a["pnl"]), sum(a["pnl"])))
        print()
    show("MAKER-ONLY (top_book)", aggM)
    show("HYBRID (taker winner + maker loser)", aggH)
    # best-of: maker in chop/reversal, hybrid in trend
    best = 0.0
    for reg, agg in (("chop", aggM), ("reversal", aggM), ("trend", aggH)):
        best += sum(agg.get(reg, {"pnl": []})["pnl"])
    print("REGIME-ROUTED (maker in chop/rev, hybrid in trend): total $%+.1f" % best)


if __name__ == "__main__":
    main()
