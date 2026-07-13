"""Detector-calibration sweep: rank chop-revoke thresholds by GATED DOLLAR PnL (not false-rate).

The entry dry-run (2026-07-13) showed the trend detector is trigger-happy: it revoked 60% of windows
on a 73%-chop night, 33% of them false (revoked trend, hindsight chop). But minimising false-rate is
the WRONG objective — the cost is asymmetric ~10:1: a false-trend-revoke on a chop window loses only
the pairs un-collected from ~revoke-time to freeze (~$0.2-0.4, the clock would cut the tail anyway),
while a MISSED trend (not revoking a real trend) rides a naked leg to ~-$3. Chasing a low false-rate
pushes thresholds up and starts missing the 10x-dearer trends. So the objective is TOTAL gated PnL in
dollars over all windows (the asymmetry is already priced in); false_trev% and missed_trend are
DIAGNOSTIC columns beside it.

Grid: dev_thresh {0.28,0.30,0.32} x confirm_sec {10,20,30} x detect_sec {100,130} x {hard,soft}
revoke, PLUS a clock-only baseline (dev=inf -> trend never fires) = 37 configs. `soft` keeps the
pre-close resting bids alive so a reverting lean refills them (a false-revoke becomes nearly free);
if soft dominates hard the answer removes threshold-sensitivity entirely. If NO detector config beats
clock-only on PnL, the detector doesn't pull its weight and clock-only wins (revocation goes in the bin).

Overfit guard (this is the SECOND calibration on the same 2224 tapes): a DAY-SLICE check — the winner
must beat-or-tie clock-only in a MAJORITY of the 12 days SEPARATELY, not just in aggregate; an edge
that sits in 2-3 days is regime-mix noise, prefer the conservative/clock-only config. The re-run
dry-run afterwards is the real out-of-sample test.

Run: POLY_MM_CACHE=... .venv/bin/python scripts/_detector_calib.py <book_jsonl> [more...]
"""
import sys
import os
import importlib.util
import collections
import statistics as st

from quoter.research.mm_tape import load_window

_SPEC = importlib.util.spec_from_file_location(
    "_config_grid", os.path.join(os.path.dirname(__file__), "_config_grid.py"))
_cg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cg)
parse_file = _cg.parse_file
gated = _cg.top_book_window_gated

DEV = [0.28, 0.30, 0.32]
CONFIRM = [10.0, 20.0, 30.0]
DETECT = [100.0, 130.0]
MODE = ["hard", "soft"]
CLOCK_ONLY_DEV = 9.9          # dev threshold |mid-0.5| can never reach -> trend never fires (baseline)
BASE = "clock-only"


def build_configs():
    """37 configs: clock-only baseline + dev x confirm x detect x {hard,soft}."""
    cfgs = [(BASE, dict(chop_dev_thresh=CLOCK_ONLY_DEV, chop_confirm_sec=10.0,
                        chop_detect_sec=100.0, revoke_mode="hard"))]
    for dev in DEV:
        for cf in CONFIRM:
            for de in DETECT:
                for mo in MODE:
                    label = "%.2f/%.0f/%.0f/%s" % (dev, cf, de, mo)
                    cfgs.append((label, dict(chop_dev_thresh=dev, chop_confirm_sec=cf,
                                             chop_detect_sec=de, revoke_mode=mo)))
    return cfgs


def main():
    cfgs = build_configs()
    pnl = collections.defaultdict(list)
    pair_eff = collections.defaultdict(list)
    n_trev = collections.Counter()          # trend-revoke count
    n_false = collections.Counter()         # trend-revoke AND hindsight chop (false positive)
    n_missed = collections.Counter()        # hindsight trend AND not trend-revoked (missed)
    day_pnl = collections.defaultdict(lambda: collections.defaultdict(float))   # label -> day -> pnl
    n_windows = 0

    for path in sys.argv[1:]:
        day = path.split("/")[-1].replace("book_", "").replace(".jsonl", "")
        snaps_by_slug = parse_file(path)
        for slug, sn in snaps_by_slug.items():
            w = load_window(slug)
            if not w or not w[0]:
                continue
            tape, winner, _ = w
            n_windows += 1
            for label, kw in cfgs:
                rec, rebate = gated(sn, tape, winner, slug,
                                    freeze_sec=45.0, chop_lookback_sec=60.0, **kw)
                pnl[label].append(rec["pnl"])
                day_pnl[label][day] += rec["pnl"]
                m = rec["pairs_merged"]
                if m > 0:
                    pair_eff[label].append(rec["pair_cost"] - rebate / m)
                trev = rec["closing_reason"] == "trend"
                hind = rec["hindsight"]
                if trev:
                    n_trev[label] += 1
                    if hind == "chop":
                        n_false[label] += 1
                elif hind == "trend":
                    n_missed[label] += 1
        snaps_by_slug = None
        print("  ...%s -> %d windows" % (day, n_windows), file=sys.stderr)

    print("\n=== detector calibration (%d windows) — ranked by TOTAL gated-PnL ===" % n_windows)
    print("objective=$PnL; false_trev%% & missed_tr are DIAGNOSTIC (cost asymmetry ~10:1 already in PnL)")
    print("%-20s %6s %10s %10s %9s %11s %9s"
          % ("cfg(dev/cf/de/mode)", "n", "tot_pnl", "pnl/win", "pair_eff", "false_trev%", "missed_tr"))
    rows = []
    for label, _ in cfgs:
        pl, pe = pnl[label], pair_eff[label]
        tot = sum(pl)
        ppw = st.mean(pl) if pl else 0.0
        mpe = st.mean(pe) if pe else float("nan")
        ftr = 100.0 * n_false[label] / n_trev[label] if n_trev[label] else 0.0
        rows.append((label, len(pl), tot, ppw, mpe, ftr, n_missed[label]))
    for r in sorted(rows, key=lambda x: -x[2]):
        print("%-20s %6d %+10.1f %+10.4f %9.4f %10.1f%% %9d" % r)

    winner = max(rows, key=lambda x: x[2])[0]
    base_tot = next(r[2] for r in rows if r[0] == BASE)
    win_tot = next(r[2] for r in rows if r[0] == winner)
    print("\n=== DAY-SLICE GUARD: winner '%s' (%+.1f) vs baseline '%s' (%+.1f) per day ==="
          % (winner, win_tot, BASE, base_tot))
    if winner == BASE:
        print("  WINNER IS THE CLOCK-ONLY BASELINE -> trend revocation does not pull its weight.")
        print("  VERDICT: drop trend-revoke; keep clock-freeze only (option C).")
        return
    days = sorted(day_pnl[winner].keys())
    need = len(days) // 2 + 1
    print("%-12s %10s %10s %8s" % ("day", "winner", "baseline", "delta"))
    wins = 0
    for d in days:
        wv, bv = day_pnl[winner][d], day_pnl[BASE][d]
        if wv >= bv - 1e-9:
            wins += 1
        print("%-12s %+10.1f %+10.1f %+8.1f" % (d, wv, bv, wv - bv))
    ok = wins >= need
    print("\nwinner beats-or-ties baseline in %d/%d days (need majority >=%d)" % (wins, len(days), need))
    print("VERDICT: %s" % ("ROBUST — edge is broad, adopt the winner"
                           if ok else "NOISE — edge sits in few days; prefer conservative / clock-only"))


if __name__ == "__main__":
    main()
