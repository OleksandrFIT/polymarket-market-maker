"""OFFLINE case-audit of the production-faithful clock-only merge-maker tactic over recorded BTC 5m
book tapes. NO live, NO orders — reads tapes, runs the gated shadow sim, and classifies every window
into an exit case, then writes a reconciled report.

FROZEN CONFIG (production-faithful, clock-only): chop_dev_thresh=9.9 (dev so high the trend gate can
never fire -> clock-only close), revoke_mode="hard", freeze_sec=45, chop_lookback_sec=60, hard_cap=True
(production skew). cap/size/link_margin stay at the sim's function defaults (6/5/0.01) which ARE the
live values; the audit asserts nothing overrides them.

Per-window classification:
  Entry gate: entry mid = _mid(first_snap["yes"]); if None or NOT in the open interval (0.35, 0.65)
  the window is NO-ENTRY -> case F (not traded, PnL 0). Otherwise run the tactic and map exit_branch:
    clean->A  completed->B  sold->C  rode_won->D  rode_lost->E

Run: POLY_MM_CACHE=... .venv/bin/python scripts/_case_audit.py <book_jsonl> [more...]
"""
import sys
import os
import importlib.util
import inspect
import subprocess
import datetime
import statistics as st

# Load the gated-shadow machinery from the sibling grid script by file path (scripts/ is not always an
# importable package depending on how python was invoked; file-location load works either way).
_SPEC = importlib.util.spec_from_file_location(
    "_config_grid", os.path.join(os.path.dirname(__file__), "_config_grid.py"))
_cg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cg)
parse_file = _cg.parse_file
top_book_window_gated = _cg.top_book_window_gated

from quoter.research.mm_tape import load_window
from quoter.research.exec_ab import _mid

# FROZEN config for every gated() call. dev 9.9 = clock-only (trend gate can't fire); hard_cap = prod skew.
FROZEN = dict(chop_dev_thresh=9.9, revoke_mode="hard", freeze_sec=45.0,
              chop_lookback_sec=60.0, hard_cap=True)

REGIMES = ["chop", "reversal", "trend"]
CASES = ["A", "B", "C", "D", "E"]
BRANCH2CASE = {"clean": "A", "completed": "B", "sold": "C", "rode_won": "D", "rode_lost": "E"}


def _assert_live_defaults():
    """cap/size/link_margin must be the sim's function defaults (the live values); assert we do not
    override them and that they equal 6/5/0.01."""
    sig = inspect.signature(top_book_window_gated)
    defs = {k: sig.parameters[k].default for k in ("cap", "size", "link_margin")}
    assert defs["cap"] == 6.0, defs
    assert defs["size"] == 5.0, defs
    assert defs["link_margin"] == 0.01, defs
    for k in ("cap", "size", "link_margin"):
        assert k not in FROZEN, "FROZEN must not override %s" % k
    return defs


def _pctile(xs, q):
    """Linear-interpolated percentile of a list (q in [0,1]). Empty -> nan."""
    if not xs:
        return float("nan")
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _mean(xs):
    return st.mean(xs) if xs else float("nan")


def audit_window(sn, slug):
    """Run the entry gate + tactic on one window's snaps. Returns a per-window dict."""
    open_ts = int(slug.rsplit("-", 1)[1])
    day = datetime.datetime.fromtimestamp(open_ts, datetime.timezone.utc).strftime("%Y-%m-%d")
    first_snap = sn[0]
    entry_mid = _mid(first_snap["yes"])
    # ENTRY GATE -> F when the open mid is missing or outside (0.35, 0.65).
    if entry_mid is None or not (0.35 < entry_mid < 0.65):
        return {"slug": slug, "day": day, "hindsight": None, "chop_hindsight": False,
                "case": "F", "pnl": 0.0, "pair_cost": None, "pair_eff": None,
                "naked_at_freeze": None, "max_pair_cost": None, "exit_branch": None,
                "entry_mid": entry_mid, "traded": False, "pairs_merged": 0.0,
                "completes": 0, "sells": 0}
    w = load_window(slug)
    if not w or not w[0]:
        return None  # no resolved outcome / no tape -> cannot score; skip (not a window we can audit)
    tape, winner, _ = w
    rec, rebate = top_book_window_gated(sn, tape, winner, slug, **FROZEN)
    m = rec["pairs_merged"]
    pair_eff = (rec["pair_cost"] - rebate / m) if (m > 0 and rec["pair_cost"] is not None) else None
    hindsight = rec["hindsight"]
    return {
        "slug": slug, "day": day, "hindsight": hindsight,
        "chop_hindsight": (hindsight == "chop"),
        "case": BRANCH2CASE[rec["exit_branch"]],
        "pnl": rec["pnl"] + rebate,                 # FULL PnL (pair/resid pnl + maker rebate revenue)
        "pair_cost": rec["pair_cost"],
        "pair_eff": pair_eff,
        "naked_at_freeze": rec["naked_at_freeze"],
        "max_pair_cost": rec["max_pair_cost"],
        "exit_branch": rec["exit_branch"],
        "entry_mid": entry_mid, "traded": True, "pairs_merged": m,
        "completes": rec["completes"], "sells": rec["sells"],
    }


def build_report(rows):
    L = []
    def p(*a):
        L.append(" ".join(str(x) for x in a))

    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=os.path.dirname(os.path.abspath(__file__)),
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        head = ""
    if not head:                       # server tapes box is not a git checkout; caller passes the hash
        head = os.environ.get("POLY_AUDIT_HEAD", "") or "(unknown — not a git checkout)"
    total = len(rows)
    traded = [r for r in rows if r["traded"]]
    f_rows = [r for r in rows if r["case"] == "F"]

    p("=" * 96)
    p("CASE AUDIT — production-faithful clock-only merge-maker (OFFLINE sim on recorded tapes, no live)")
    p("=" * 96)
    p("HEAD commit:", head)
    p("FROZEN config:", ", ".join("%s=%s" % (k, v) for k, v in FROZEN.items()),
      "| cap=6.0 size=5.0 link_margin=0.01 (function defaults = live values, asserted)")
    p("Live-faithful knobs: hard_cap=True, naked_cap=6, size=5, NO early phase, completion NEAR-END")
    p("  only, clock-only (chop_trend_revoke=False via dev=9.9), freeze_sec=45, replace_shift=0.02,")
    p("  dwell=4, link_margin=0.01.")
    p("NOTE: the sim does NOT model FOK-kill. A rode_* (D/E) case means the near-end price CONDITION")
    p("  was never met (no bid to sell into / pair>=$1 with no completable light leg / budget out),")
    p("  NOT a FOK that was tried and killed. Live FOK-kill is a separate, unmodelled tail.")
    p("Total windows audited:", total, " traded:", len(traded), " no-entry(F):", len(f_rows))
    p("")

    # ---- 1. REGIME x CASE matrix -----------------------------------------------------------------
    p("-" * 96)
    p("1. REGIME x CASE matrix (rows: hindsight regime; cols: exit case)")
    p("-" * 96)
    p("   each cell: n | %%row | meanPnL | tot$ | mean naked@frz | mean pair_eff")
    hdr = "%-10s" % "regime"
    for c in CASES:
        hdr += " | %-34s" % ("case " + c)
    p(hdr)
    for reg in REGIMES:
        reg_rows = [r for r in traded if r["hindsight"] == reg]
        row_tot = len(reg_rows)
        line = "%-10s" % reg
        for c in CASES:
            cell = [r for r in reg_rows if r["case"] == c]
            n = len(cell)
            pct = 100.0 * n / row_tot if row_tot else 0.0
            mpnl = _mean([r["pnl"] for r in cell])
            tot = sum(r["pnl"] for r in cell)
            mnf = _mean([r["naked_at_freeze"] for r in cell if r["naked_at_freeze"] is not None])
            mpe = _mean([r["pair_eff"] for r in cell if r["pair_eff"] is not None])
            flag = "!" if (0 < n < 30) else " "
            line += " | %2d %4.0f%% %+6.2f %+7.1f %4.1f %6.4f%s" % (
                n, pct, mpnl, tot, mnf, mpe, flag)
        p(line + "   (rowN=%d)" % row_tot)
    # windows whose hindsight is None / other (still traded) — for completeness of the traded set
    other = [r for r in traded if r["hindsight"] not in REGIMES]
    if other:
        p("%-10s traded windows with hindsight None/other: n=%d  tot$=%+.1f"
          % ("(other)", len(other), sum(r["pnl"] for r in other)))
    p("%-10s no-entry (gate rejected): n=%d  PnL=$0" % ("F", len(f_rows)))
    # chop(hindsight) go/no-go-relevant subset
    chop_h = [r for r in traded if r["chop_hindsight"]]
    chop_pe = [r["pair_eff"] for r in chop_h if r["pair_eff"] is not None]
    p("chop(hindsight) GO/NO-GO subset: n=%d  n(pair_eff)=%d  mean pair_eff=%.4f"
      % (len(chop_h), len(chop_pe), _mean(chop_pe)))
    p("  NOTE: live's causal-chop is measured by detector=='chop'; clock-only run has no detector")
    p("  trend, so ALL traded windows are 'acted-chop'. This subset approximates the go/no-go cohort")
    p("  by HINDSIGHT-chop (post-hoc), not the live detector label.")
    p("")

    # ---- 2. NAKED deep-dive ----------------------------------------------------------------------
    p("-" * 96)
    p("2. NAKED-AT-FREEZE deep-dive (traded windows arriving at freeze with naked >= 1)")
    p("-" * 96)
    deep = [r for r in traded if r["naked_at_freeze"] is not None and r["naked_at_freeze"] >= 1]
    p("(a) share of TRADED windows with naked@freeze >= 1:")
    p("    overall: %d / %d = %.1f%%" % (len(deep), len(traded),
                                          100.0 * len(deep) / len(traded) if traded else 0.0))
    for reg in REGIMES:
        rt = [r for r in traded if r["hindsight"] == reg]
        rd = [r for r in rt if r["naked_at_freeze"] is not None and r["naked_at_freeze"] >= 1]
        p("    %-9s: %d / %d = %.1f%%" % (reg, len(rd), len(rt),
                                           100.0 * len(rd) / len(rt) if rt else 0.0))
    p("(b) of those naked>=1, branch mix (B/C/D/E) per regime:")
    for reg in REGIMES:
        rd = [r for r in deep if r["hindsight"] == reg]
        n = len(rd)
        if not n:
            p("    %-9s: (none)" % reg)
            continue
        mix = {c: sum(1 for r in rd if r["case"] == c) for c in ("B", "C", "D", "E")}
        p("    %-9s (n=%d): B %4.0f%%  C %4.0f%%  D %4.0f%%  E %4.0f%%" % (
            reg, n, 100.0 * mix["B"] / n, 100.0 * mix["C"] / n,
            100.0 * mix["D"] / n, 100.0 * mix["E"] / n))
    p("(c) conditional PnL of each branch per regime (mean / p50 / p90 / worst):")
    for reg in REGIMES:
        for c in ("B", "C", "D", "E"):
            cell = [r for r in deep if r["hindsight"] == reg and r["case"] == c]
            if not cell:
                continue
            pnls = [r["pnl"] for r in cell]
            p("    %-9s %s (n=%2d): mean %+6.2f  p50 %+6.2f  p90 %+6.2f  worst %+6.2f" % (
                reg, c, len(cell), _mean(pnls), _pctile(pnls, 0.5),
                _pctile(pnls, 0.9), min(pnls)))
    p("(d) histogram of naked@freeze (traded windows), and cap invariant (must be <= 6):")
    naf = [r["naked_at_freeze"] for r in traded if r["naked_at_freeze"] is not None]
    bins = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 1e9)]
    for lo, hi in bins:
        cnt = sum(1 for x in naf if lo <= x < hi)
        lab = ">=6 (VIOLATION)" if lo == 6 else "[%d,%d)" % (lo, hi)
        p("    %-16s %4d  %5.1f%%" % (lab, cnt, 100.0 * cnt / len(naf) if naf else 0.0))
    max_naf = max(naf) if naf else 0.0
    over6 = [r for r in traded if r["naked_at_freeze"] is not None and r["naked_at_freeze"] > 6]
    p("    max naked@freeze = %.2f" % max_naf)
    if over6:
        p("    *** FLAG: %d window(s) with naked@freeze > 6 — HARD_CAP FAILED ***" % len(over6))
        for r in over6:
            p("        %s  naked@freeze=%.2f" % (r["slug"], r["naked_at_freeze"]))
    else:
        p("    OK: max naked@freeze <= 6 everywhere (hard_cap holds).")
    p("(e) INVARIANT — merged windows with max_pair_cost >= 1.00 (expected 0):")
    p("    max_pair_cost = TRANSIENT max of (avg_up + avg_dn) observed at any tick BEFORE a merge.")
    p("    realized pair_cost = merged_cost/merged = the actual per-pair cost the window booked.")
    viol = [r for r in traded if r["pairs_merged"] > 0 and r["max_pair_cost"] is not None
            and r["max_pair_cost"] >= 1.0]
    realized_viol = [r for r in viol if r["pair_cost"] is not None and r["pair_cost"] >= 1.0]
    if viol:
        p("    STOP: linked-pair cap violated (pre-registered rule) — %d merged window(s) had a" % len(viol))
        p("    transient max_pair_cost >= $1.00. Of these, %d also booked a REALIZED pair_cost >= $1.00."
          % len(realized_viol))
        p("    NOTE: 'pairs' is total pairs merged in the window (fractional; a <1 value means the")
        p("    breach touched only a sub-unit merge).")
        p("    %-30s %11s %11s %6s %5s %5s %8s" %
          ("slug", "max_pairc", "realiz_pc", "pairs", "comp", "sell", "pnl$"))
        for r in sorted(viol, key=lambda x: -x["max_pair_cost"]):
            p("        %-26s %11.4f %11s %6.1f %5d %5d %+8.2f" % (
                r["slug"], r["max_pair_cost"],
                ("%.4f" % r["pair_cost"]) if r["pair_cost"] is not None else "None",
                r["pairs_merged"], r["completes"], r["sells"], r["pnl"]))
        p("    CONTEXT (honest): the transient breach is a few marginal units whose blended per-side")
        p("    averages momentarily summed > $1; the linked light-cap only binds when TOPPING the")
        p("    SHORTER leg (inv[other]>inv[side]) and the near-end completion uses a 0-margin threshold")
        p("    (heavy_avg+lap<1.0), so both legs filled near their tops can transiently sum > $1.")
        p("    Whether this blocks live is a pre-registration call: by the LITERAL max_pair_cost rule it")
        p("    STOPS; by REALIZED pair_cost (%d window(s) >= $1) the economic loss is bounded. Reported"
          % len(realized_viol))
        p("    both — the number wins; do not paper over. Fix candidate: apply link_margin to the")
        p("    completion threshold + a two-sided blended-cost guard, then re-verify.")
    else:
        p("    OK: 0 merged windows with max_pair_cost >= $1.00 (linked-pair cap holds).")
    p("")

    # ---- 3. per-case PnL percentiles -------------------------------------------------------------
    p("-" * 96)
    p("3. PER-CASE PnL percentiles ($/window)")
    p("-" * 96)
    p("%-6s %5s %8s %8s %8s %8s %8s %8s %8s" %
      ("case", "n", "p05", "p25", "p50", "p75", "p95", "min", "max"))
    for c in CASES:
        cell = [r for r in traded if r["case"] == c]
        pnls = [r["pnl"] for r in cell]
        if not pnls:
            p("%-6s %5d   (no windows)" % (c, 0))
            continue
        p("%-6s %5d %+8.2f %+8.2f %+8.2f %+8.2f %+8.2f %+8.2f %+8.2f" % (
            c, len(pnls), _pctile(pnls, 0.05), _pctile(pnls, 0.25), _pctile(pnls, 0.5),
            _pctile(pnls, 0.75), _pctile(pnls, 0.95), min(pnls), max(pnls)))
    p("")

    # ---- 4. 10 worst windows ---------------------------------------------------------------------
    p("-" * 96)
    p("4. 10 WORST windows by full PnL")
    p("-" * 96)
    worst = sorted(traded, key=lambda r: r["pnl"])[:10]
    p("%-30s %-11s %-9s %-5s %-12s %-11s %8s" %
      ("slug", "day", "regime", "case", "naked@frz", "exit_branch", "$"))
    for r in worst:
        p("%-30s %-11s %-9s %-5s %-12.2f %-11s %+8.2f" % (
            r["slug"], r["day"], str(r["hindsight"]), r["case"],
            r["naked_at_freeze"] if r["naked_at_freeze"] is not None else float("nan"),
            r["exit_branch"], r["pnl"]))
    p("")

    # ---- 5. pair_eff sigma for n-sizing ----------------------------------------------------------
    p("-" * 96)
    p("5. pair_eff sigma for n-sizing (chop-hindsight subset)")
    p("-" * 96)
    sigma = st.pstdev(chop_pe) if len(chop_pe) > 1 else float("nan")
    p("chop(hindsight) per-window pair_eff: n=%d  mean=%.4f  sigma(pop)=%.4f"
      % (len(chop_pe), _mean(chop_pe), sigma))
    p("APPROXIMATION: pairs within a window are correlated, so we approximate the per-PAIR sigma by")
    p("  the per-WINDOW pair_eff sigma (conservative). SE(mean) = sigma / sqrt(n).")
    for n in (50, 80, 100):
        se = sigma / (n ** 0.5) if sigma == sigma else float("nan")
        p("    n=%3d pairs -> SE of mean pair_eff = %.4f" % (n, se))
    p("")

    # ---- 6. RECONCILIATION -----------------------------------------------------------------------
    p("-" * 96)
    p("6. RECONCILIATION (must balance to the cent)")
    p("-" * 96)
    case_counts = {c: sum(1 for r in rows if r["case"] == c) for c in ["F"] + CASES}
    sum_counts = sum(case_counts.values())
    p("   case counts: " + "  ".join("%s=%d" % (c, case_counts[c]) for c in ["F"] + CASES))
    p("   Sigma(counts across F,A,B,C,D,E) = %d   total windows = %d   diff = %d"
      % (sum_counts, total, sum_counts - total))
    if sum_counts != total:
        p("   RECONCILIATION FAILED — audit bug (counts do not sum to total)")
    sum_cases_dollar = sum(sum(r["pnl"] for r in rows if r["case"] == c) for c in ["F"] + CASES)
    agg_pnl = sum(r["pnl"] for r in rows)
    diff = sum_cases_dollar - agg_pnl
    p("   Sigma($ across all cases) = %+.2f   aggregate PnL (all windows) = %+.2f   diff = %+.4f"
      % (sum_cases_dollar, agg_pnl, diff))
    if abs(diff) > 0.01:
        p("   RECONCILIATION FAILED — audit bug ($ mismatch > $0.01)")
    else:
        p("   OK: reconciliation balances (counts exact, $ within $0.01).")
    p("")

    # ---- 7. frequencies vs spec §4 ---------------------------------------------------------------
    p("-" * 96)
    p("7. MEASURED case frequencies vs spec §4 (A~51% clean-ish, D/E trend tail ~20%)")
    p("-" * 96)
    if traded:
        fa = 100.0 * case_counts["A"] / len(traded)
        fde = 100.0 * (case_counts["D"] + case_counts["E"]) / len(traded)
        p("   measured (share of TRADED): A=%.1f%%  B=%.1f%%  C=%.1f%%  D=%.1f%%  E=%.1f%%"
          % tuple(100.0 * case_counts[c] / len(traded) for c in CASES))
        p("   spec: A~51%% clean-ish ; D+E trend tail ~20%%")
        p("   measured A=%.1f%% (spec ~51%%) ; measured D+E=%.1f%% (spec ~20%%)" % (fa, fde))
        p("   -> the DOC (spec), not the numbers, is corrected where they differ.")
    p("")

    # ---- 8. MEASURED vs ASSUMED ------------------------------------------------------------------
    p("-" * 96)
    p("8. MEASURED vs ASSUMED / reliability")
    p("-" * 96)
    p("   Any regime x case cell with 0 < n < 30 is marked '!' in the matrix above -> unreliable (n<30).")
    unreliable = []
    for reg in REGIMES:
        for c in CASES:
            n = sum(1 for r in traded if r["hindsight"] == reg and r["case"] == c)
            if 0 < n < 30:
                unreliable.append("%s/%s(n=%d)" % (reg, c, n))
    p("   unreliable cells: " + (", ".join(unreliable) if unreliable else "(none)"))
    p("   MEASURED numbers above supersede any prior report/spec where they disagree (number wins).")
    p("=" * 96)

    return "\n".join(L)


def main():
    _assert_live_defaults()
    tapes = sys.argv[1:]
    if not tapes:
        print("usage: _case_audit.py <book_jsonl> [more...]", file=sys.stderr)
        sys.exit(2)
    rows = []
    for path in tapes:
        snaps_by_slug = parse_file(path)
        for slug, sn in snaps_by_slug.items():
            if not sn:
                continue
            r = audit_window(sn, slug)
            if r is not None:
                rows.append(r)
        snaps_by_slug = None
        print("  ...%s -> %d windows so far" % (path.split("/")[-1], len(rows)), file=sys.stderr)

    report = build_report(rows)
    print(report)
    out_path = os.path.join(os.path.dirname(__file__), "_case_audit_report.md")
    with open(out_path, "w") as fh:
        fh.write(report + "\n")
    print("\n[report written to %s]" % out_path, file=sys.stderr)


if __name__ == "__main__":
    main()
