"""Per-hindsight-regime PnL decomposition of clock-only + the full per-window distribution — the
complete risk picture before a live run. Groups every window by its post-hoc regime (chop/reversal/
trend via the shared window_regime, exposed as rec["hindsight"]) and shows, per regime: merge-gross,
rebate, naked drag, NET (with rebate), % of windows that lose, and the worst single window. Then the
overall per-window percentile distribution (tail risk) and the count/size of losing windows.
Clock-only = dev 9.9 (trend never fires). Run: POLY_MM_CACHE=... .venv/bin/python scripts/_regime_split.py <book>..."""
import sys
import os
import importlib.util
import collections

from quoter.research.mm_tape import load_window

_SPEC = importlib.util.spec_from_file_location(
    "_config_grid", os.path.join(os.path.dirname(__file__), "_config_grid.py"))
_cg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cg)

CFG = dict(chop_dev_thresh=9.9, revoke_mode="hard", freeze_sec=45.0, chop_lookback_sec=60.0,
           hard_cap=True)   # PRODUCTION-FAITHFUL skew (naked capped at 6, not the looser sim overshoot)


def _blank():
    return {"n": 0, "mg": 0.0, "reb": 0.0, "nk": 0.0, "pnl": 0.0, "neg": 0,
            "worst": 0.0, "best": 0.0, "pairs": 0.0, "nkwon": 0, "nklost": 0, "nkflat": 0}


def main():
    by = collections.defaultdict(_blank)
    allpnl = []
    for path in sys.argv[1:]:
        snaps = _cg.parse_file(path)
        for slug, sn in snaps.items():
            w = load_window(slug)
            if not w or not w[0]:
                continue
            tape, winner, _ = w
            rec, reb = _cg.top_book_window_gated(sn, tape, winner, slug, **CFG)
            reg = rec.get("hindsight") or "unknown"
            m = rec["pairs_merged"]
            pnl = rec["pnl"]
            mg = m * (1 - rec["pair_cost"]) if (m > 0 and rec["pair_cost"] is not None) else 0.0
            nk = pnl - mg
            full = pnl + reb                              # WITH rebate = economic PnL
            d = by[reg]
            d["n"] += 1
            d["mg"] += mg
            d["reb"] += reb
            d["nk"] += nk
            d["pnl"] += full
            d["pairs"] += m
            if full < -0.01:
                d["neg"] += 1
            d["worst"] = min(d["worst"], full)
            d["best"] = max(d["best"], full)
            ro = rec["resid_outcome"]
            d["nkwon"] += ro == "WON"
            d["nklost"] += ro == "LOST"
            d["nkflat"] += ro == "flat"
            allpnl.append(full)
        snaps = None
        print("  ...%s" % path.split("/")[-1], file=sys.stderr)

    N = sum(d["n"] for d in by.values())
    if not N:
        print("no windows")
        return
    print("\n=== per-regime decomposition (%d windows, $/window, WITH rebate) ===" % N)
    print("%-9s %6s %4s %8s %8s %8s %8s %6s %7s %7s"
          % ("regime", "n", "%", "merge", "rebate", "naked", "NET", "%neg", "worst", "best"))
    for reg in ("chop", "reversal", "trend", "unknown"):
        if reg not in by:
            continue
        d = by[reg]
        n = d["n"]
        print("%-9s %6d %3.0f%% %+8.4f %+8.4f %+8.4f %+8.4f %5.0f%% %+7.2f %+7.2f"
              % (reg, n, 100 * n / N, d["mg"] / n, d["reb"] / n, d["nk"] / n, d["pnl"] / n,
                 100 * d["neg"] / n, d["worst"], d["best"]))
    print("\nnaked-leg outcome by regime (won/lost/flat windows):")
    for reg in ("chop", "reversal", "trend"):
        if reg not in by:
            continue
        d = by[reg]
        res = d["nkwon"] + d["nklost"]
        print("  %-9s WON %d / LOST %d / flat %d  (%.0f%% of resolved-naked LOST)"
              % (reg, d["nkwon"], d["nklost"], d["nkflat"],
                 100 * d["nklost"] / res if res else 0))

    allpnl.sort()
    print("\n=== per-window distribution (%d windows, $/window, WITH rebate) ===" % len(allpnl))
    for pct in (1, 5, 10, 25, 50, 75, 90, 95, 99):
        print("  p%02d: %+.3f" % (pct, allpnl[min(len(allpnl) - 1, int(len(allpnl) * pct / 100))]))
    print("  min %+.2f   max %+.2f   mean %+.4f" % (allpnl[0], allpnl[-1], sum(allpnl) / len(allpnl)))
    neg = sum(1 for x in allpnl if x < -0.01)
    negsum = sum(x for x in allpnl if x < -0.01)
    print("  negative windows: %d/%d = %.0f%%  (their total: $%.1f, avg $%.3f)"
          % (neg, len(allpnl), 100 * neg / len(allpnl), negsum, negsum / max(neg, 1)))
    tail = [x for x in allpnl if x < -1.0]
    print("  windows worse than -$1: %d (%.1f%%)  worse than -$2: %d"
          % (len(tail), 100 * len(tail) / len(allpnl), sum(1 for x in allpnl if x < -2.0)))


if __name__ == "__main__":
    main()
