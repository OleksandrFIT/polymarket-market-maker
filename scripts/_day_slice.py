"""Day-slice robustness check for the CHOSEN chop-gate config (0.02/45/60): run that one config
per DAY (one book file = one day) and report the per-day distribution of pair_eff and PnL/window.

Purpose (the point the grid can't make on its own): if the std of DAILY pair_eff exceeds the grid's
config spread (~0.6c across all 27 configs), then the grid did NOT distinguish configs above
day-to-day regime noise -> 'center defaults' is the only honest pick, no gaming argument needed. The
day-slice also surfaces (a) the regime-mix drift (PnL/window over the wider 1-12 Jul window is lower
than the favourable 4-9 Jul slice) so the live expectation anchor is set correctly, and (b) any
near-zero / negative days, known IN ADVANCE so a bad first live day is not misread as the mechanism
breaking.

Run: POLY_MM_CACHE=... .venv/bin/python scripts/_day_slice.py <book_jsonl> [more...]
"""
import sys
import os
import importlib.util
import statistics as st

from quoter.research.mm_tape import load_window

# Load the gated-shadow machinery from the sibling grid script by file path (scripts/ is not always
# an importable package depending on how python was invoked; file-location load works either way).
_SPEC = importlib.util.spec_from_file_location(
    "_config_grid", os.path.join(os.path.dirname(__file__), "_config_grid.py"))
_cg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cg)
parse_file = _cg.parse_file
top_book_window_gated = _cg.top_book_window_gated

CFG = dict(replace_shift=0.02, freeze_sec=45.0, chop_lookback_sec=60.0)
GRID_SPREAD = 0.006          # the grid's pair_eff spread across all 27 configs (0.8911..0.8970)
WEAK_PNL = 0.10              # flag a day whose pnl/window <= this (near-zero / negative)


def day_stats(snaps_by_slug):
    """Aggregate one day's windows under CFG. Returns (n_windows, n_pairs, mean_pair_eff, mean_pnl,
    total_pnl). mean_pair_eff is nan when no window merged a pair."""
    effs, pnls, pairs = [], [], 0
    for slug, sn in snaps_by_slug.items():
        w = load_window(slug)
        if not w or not w[0]:
            continue
        tape, winner, _ = w
        rec, rebate = top_book_window_gated(sn, tape, winner, slug, **CFG)
        m = rec["pairs_merged"]
        if m > 0:
            effs.append(rec["pair_cost"] - rebate / m)
            pairs += m
        pnls.append(rec["pnl"])
    m_eff = st.mean(effs) if effs else float("nan")
    m_pnl = st.mean(pnls) if pnls else 0.0
    return len(pnls), int(pairs), m_eff, m_pnl, sum(pnls)


def main():
    print("=== day-slice: config 0.02/45/60 (the chosen live config), one book file = one day ===")
    print("%-12s %6s %7s %10s %12s %10s"
          % ("day", "n_win", "n_pair", "pair_eff", "pnl/window", "tot_pnl"))
    daily_eff, daily_pnl_w, weak = [], [], []
    for path in sys.argv[1:]:
        n_win, n_pair, m_eff, m_pnl, t_pnl = day_stats(parse_file(path))
        day = path.split("/")[-1].replace("book_", "").replace(".jsonl", "")
        print("%-12s %6d %7d %10.4f %12.4f %+10.1f" % (day, n_win, n_pair, m_eff, m_pnl, t_pnl))
        if m_eff == m_eff:                       # not nan
            daily_eff.append(m_eff)
        daily_pnl_w.append(m_pnl)
        if m_pnl <= WEAK_PNL:
            weak.append((day, m_pnl))

    print("\n=== daily distribution (population std over days) ===")
    if daily_eff:
        print("pair_eff : mean %.4f  std %.4f  min %.4f  max %.4f"
              % (st.mean(daily_eff), st.pstdev(daily_eff), min(daily_eff), max(daily_eff)))
    print("pnl/win  : mean %.4f  std %.4f  min %.4f  max %.4f"
          % (st.mean(daily_pnl_w), st.pstdev(daily_pnl_w), min(daily_pnl_w), max(daily_pnl_w)))

    day_std = st.pstdev(daily_eff) if len(daily_eff) > 1 else 0.0
    print("\nVERDICT:")
    if day_std > GRID_SPREAD:
        print("  daily pair_eff std %.4f  >  grid config spread %.4f" % (day_std, GRID_SPREAD))
        print("  -> the grid did NOT distinguish configs above day-to-day noise.")
        print("  -> center defaults (0.02/45/60) are the honest pick; tuning these knobs is noise.")
    else:
        print("  daily pair_eff std %.4f  <=  grid config spread %.4f" % (day_std, GRID_SPREAD))
        print("  -> config choice MAY matter above day noise; revisit the ranking.")

    if weak:
        print("\nNEAR-ZERO / NEGATIVE days (pnl/window <= $%.2f) — EXPECTED (regime mix), know before live:"
              % WEAK_PNL)
        for day, m_pnl in weak:
            print("  %s  pnl/window %+.4f" % (day, m_pnl))


if __name__ == "__main__":
    main()
