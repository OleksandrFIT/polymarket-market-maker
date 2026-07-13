"""Decompose clock-only PnL into merge-gross (pairs), rebate, and naked-residual outcome — the
ratio the +$0.32/window headline hides. window_record's pnl = merged + redeem - spent already nets
the naked leg (a lost naked = its cost in `spent` with no redeem); this splits that net into:
  merge_gross  = merged * (1 - pair_cost)          # pure pair profit, NO rebate
  naked_outcome = pnl - merge_gross                # redeem(won) - cost(lost) of the unpaired residual
  rebate        = summed maker rebate (2nd stream, not in window_record.pnl)
Clock-only = dev_thresh 9.9 (trend never fires -> only clock closes), matching the live config.
Run: POLY_MM_CACHE=... .venv/bin/python scripts/_pnl_split.py <book_jsonl> [more...]
"""
import sys
import os
import importlib.util

from quoter.research.mm_tape import load_window

_SPEC = importlib.util.spec_from_file_location(
    "_config_grid", os.path.join(os.path.dirname(__file__), "_config_grid.py"))
_cg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cg)

CFG = dict(chop_dev_thresh=9.9, revoke_mode="hard", freeze_sec=45.0, chop_lookback_sec=60.0)


def main():
    tot_pairs = tot_mg = tot_reb = tot_nk = tot_pnl = 0.0
    nk_won = nk_lost = nk_flat = 0
    nk_won_sh = nk_lost_sh = 0.0
    win_pos = win_neg = win_zero = 0
    nwin = 0
    for path in sys.argv[1:]:
        snaps = _cg.parse_file(path)
        for slug, sn in snaps.items():
            w = load_window(slug)
            if not w or not w[0]:
                continue
            tape, winner, _ = w
            nwin += 1
            rec, reb = _cg.top_book_window_gated(sn, tape, winner, slug, **CFG)
            m = rec["pairs_merged"]
            pnl = rec["pnl"]
            mg = m * (1 - rec["pair_cost"]) if (m > 0 and rec["pair_cost"] is not None) else 0.0
            nk = pnl - mg
            tot_pairs += m
            tot_mg += mg
            tot_reb += reb
            tot_nk += nk
            tot_pnl += pnl
            full = pnl + reb
            win_pos += full > 0.01
            win_neg += full < -0.01
            win_zero += abs(full) <= 0.01
            ro = rec["resid_outcome"]
            nr = abs(rec["naked_resid"])
            if ro == "WON":
                nk_won += 1
                nk_won_sh += nr
            elif ro == "LOST":
                nk_lost += 1
                nk_lost_sh += nr
            else:
                nk_flat += 1
        snaps = None
        print("  ...%s -> %d win" % (path.split("/")[-1], nwin), file=sys.stderr)

    if not nwin:
        print("no windows")
        return
    gpos = tot_mg + tot_reb
    print("\n=== clock-only PnL decomposition (%d windows) ===" % nwin)
    print("%-34s %12s %12s" % ("component", "total $", "$/window"))
    print("%-34s %12.1f %12.4f" % ("merge-gross (pairs, no rebate)", tot_mg, tot_mg / nwin))
    print("%-34s %12.1f %12.4f" % ("rebate (2nd stream)", tot_reb, tot_reb / nwin))
    print("%-34s %12.1f %12.4f" % ("= gross positive", gpos, gpos / nwin))
    print("%-34s %12.1f %12.4f" % ("naked residual outcome", tot_nk, tot_nk / nwin))
    print("%-34s %12.1f %12.4f" % ("= NET PnL (with rebate)", tot_pnl + tot_reb, (tot_pnl + tot_reb) / nwin))
    print("%-34s %12.1f %12.4f" % ("  (PnL without rebate)", tot_pnl, tot_pnl / nwin))
    print("\npairs merged: %.0f total (%.1f/window)" % (tot_pairs, tot_pairs / nwin))
    print("naked windows: WON %d / LOST %d / flat %d  (%.0f%% lost)"
          % (nk_won, nk_lost, nk_flat, 100 * nk_lost / max(nk_won + nk_lost, 1)))
    print("naked shares: WON %.0f / LOST %.0f  (ratio lost:won = %.2f)"
          % (nk_won_sh, nk_lost_sh, nk_lost_sh / max(nk_won_sh, 1)))
    print("windows net: +%d / -%d / ~0 %d" % (win_pos, win_neg, win_zero))
    print("\nRATIO:")
    print("  naked drag eats %.0f%% of gross positive (merge+rebate)" % (-tot_nk / gpos * 100))
    print("  net PnL = %.0f%% of gross positive" % ((tot_pnl + tot_reb) / gpos * 100))
    print("  merge-gross : naked-drag = %.2f : 1" % (tot_mg / max(-tot_nk, 1e-9)))


if __name__ == "__main__":
    main()
