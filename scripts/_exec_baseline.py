"""OFFLINE execution BASELINE for the frozen clock-only chop-gate config. Read-only over recorded
BTC 5m book tapes. Produces two shadow baselines so a LIVE result has something to compare against —
this is NOT an optimization pass and nothing here is tuned.

(1) E-SUBDIVISION — of the windows whose naked leg RODE to resolution (exit_branch rode_won/rode_lost),
    WHY did the near-end close not execute? The sim does NOT model FOK-kill (it executes whenever the
    price CONDITION holds), so every reason is STRUCTURAL:
      no_near_end_snap  - the tape had no snapshot inside the near-end window (data gap)
      no_bid_on_loser   - completion failed and the heavy/loser book had NO bid -> nothing to sell into
      budget            - completion was priced fine but the budget check blocked it
      other:*           - anything else
    The decision this table drives: the share of E that is `no_bid_on_loser` is structurally
    UNFIXABLE post-hoc (there is nothing left to sell), so it can only be attacked UPSTREAM by
    accumulating less naked. The rest is what more persistent execution could in principle reach.

(2) SELL_RECOVERY SHADOW BASELINE — for windows that SOLD (case C), what fraction of the leg's
    freeze-time value did the sell recover?  recovery = sell_px / mid_at_freeze(sold side).
    CRITICAL: the shadow ALWAYS executes the sell when a bid exists; LIVE can have the FOK killed, so
    live recovery will be WORSE. This baseline is an UPPER BOUND for the live sell_px_avg telemetry.

Run: POLY_MM_CACHE=... .venv/bin/python scripts/_exec_baseline.py <book_jsonl> [more...]
"""
import sys
import os
import subprocess
import importlib.util
import statistics as st
import collections

from quoter.research.mm_tape import load_window

# Load the gated-shadow machinery from the sibling grid script by file path (scripts/ is not always
# an importable package depending on how python was invoked; file-location load works either way).
_SPEC = importlib.util.spec_from_file_location(
    "_config_grid", os.path.join(os.path.dirname(__file__), "_config_grid.py"))
_cg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cg)
parse_file = _cg.parse_file
top_book_window_gated = _cg.top_book_window_gated

# FROZEN production-mirroring config. dev 9.9 = clock-only (the trend revoke never fires);
# hard_cap = the production skew_ok. cap/size/link_margin stay at the function defaults (6/5/0.01),
# which ARE the live values. Do NOT tune any of this — it is a baseline, not a search.
CFG = dict(chop_dev_thresh=9.9, revoke_mode="hard", freeze_sec=45.0,
           chop_lookback_sec=60.0, hard_cap=True)

REGIMES = ("chop", "reversal", "trend")
REASONS = ("no_near_end_snap", "no_bid_on_loser", "budget", "other")
NAKED_BUCKETS = (("[1,3)", 1.0, 3.0), ("[3,5)", 3.0, 5.0), ("[5,6]", 5.0, 6.0001))


def _reason_col(r):
    """Fold the sim's specific `other:*` tags into the report's single `other` column."""
    return r if r in REASONS else "other"


def _heavy_side(rec):
    """The leg left naked at resolution (the one we would have had to sell)."""
    n = rec["naked_resid"]
    return "Up" if n > 0 else ("Down" if n < 0 else None)


def _pct(x, y):
    return (100.0 * x / y) if y else float("nan")


def _q(vals, p):
    """Simple nearest-rank percentile (no interpolation); vals must be non-empty."""
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[i]


def collect(paths):
    """Run the frozen config over every window of every tape. Returns the list of recs.
    Memory-safe: parse one book file at a time and free it before the next."""
    recs = []
    for path in paths:
        snaps_by_slug = parse_file(path)
        for slug, sn in snaps_by_slug.items():
            w = load_window(slug)
            if not w or not w[0]:
                continue
            tape, winner, _ = w
            rec, _rebate = top_book_window_gated(sn, tape, winner, slug, **CFG)
            recs.append(rec)
        snaps_by_slug = None
        print("  ...%s -> %d windows so far" % (path.split("/")[-1], len(recs)), file=sys.stderr)
    return recs


def report_e_subdivision(recs, out):
    e = [r for r in recs if r["exit_branch"] in ("rode_won", "rode_lost")]
    out("## (1) E-SUBDIVISION — why the near-end close never executed")
    out("")
    out("Windows total: %d.  E (rode to resolution): %d (%.1f%% of all windows)."
        % (len(recs), len(e), _pct(len(e), len(recs))))
    out("")
    out("The sim does NOT model FOK-kill: it executes the near-end close whenever the price CONDITION")
    out("holds. Every reason below is therefore STRUCTURAL, not 'an order was tried and killed'.")
    out("Live FOK-kill is a separate, unmodelled tail (production logs sell_kills/complete_kills).")
    out("")
    if not e:
        out("_No E windows._")
        return
    by = collections.defaultdict(list)
    for r in e:
        by[(r["hindsight"], _reason_col(r["e_reason"]))].append(r)

    out("### count / %% of that regime's E / mean PnL")
    out("")
    out("| regime | " + " | ".join(REASONS) + " | regime E total |")
    out("|" + "---|" * (len(REASONS) + 2))
    for reg in REGIMES:
        n_reg = sum(len(by[(reg, c)]) for c in REASONS)
        cells = []
        for c in REASONS:
            g = by[(reg, c)]
            cells.append("—" if not g else "%d / %.0f%% / %+.2f"
                         % (len(g), _pct(len(g), n_reg), st.mean([x["pnl"] for x in g])))
        out("| %s | %s | %d |" % (reg, " | ".join(cells), n_reg))
    tot = []
    for c in REASONS:
        g = [r for r in e if _reason_col(r["e_reason"]) == c]
        tot.append("—" if not g else "%d / %.0f%% / %+.2f"
                   % (len(g), _pct(len(g), len(e)), st.mean([x["pnl"] for x in g])))
    out("| **ALL** | %s | %d |" % (" | ".join(tot), len(e)))
    out("")

    out("### per-reason: mean naked_at_freeze and mean LOSER price at freeze")
    out("")
    out("(loser = the leg left naked; its price at freeze is mid_up/mid_dn_at_freeze for that side.")
    out(" `_mid` falls back to the single present side when the book is one-sided.)")
    out("")
    out("| reason | n | %% of E | mean PnL | mean naked_at_freeze | mean loser px @freeze | n px |")
    out("|---|---|---|---|---|---|---|")
    for c in REASONS:
        g = [r for r in e if _reason_col(r["e_reason"]) == c]
        if not g:
            out("| %s | 0 | — | — | — | — | 0 |" % c)
            continue
        px = []
        for r in g:
            h = _heavy_side(r)
            m = r["mid_up_at_freeze"] if h == "Up" else r["mid_dn_at_freeze"]
            if m is not None:
                px.append(m)
        out("| %s | %d | %.1f%% | %+.2f | %.2f | %s | %d |"
            % (c, len(g), _pct(len(g), len(e)),
               st.mean([x["pnl"] for x in g]),
               st.mean([x["naked_at_freeze"] for x in g]),
               ("%.3f" % st.mean(px)) if px else "—", len(px)))
    out("")

    nb = [r for r in e if _reason_col(r["e_reason"]) == "no_bid_on_loser"]
    rest = [r for r in e if _reason_col(r["e_reason"]) != "no_bid_on_loser"]
    out("### HEADLINE")
    out("")
    out("**no_bid_on_loser = %d / %d E windows = %.1f%% of E** (mean PnL %s) — structurally UNFIXABLE"
        % (len(nb), len(e), _pct(len(nb), len(e)),
           ("%+.2f" % st.mean([x["pnl"] for x in nb])) if nb else "—"))
    out("post-hoc: the loser book has no bid, there is nothing left to sell into. This part of the E")
    out("tail can only be attacked UPSTREAM (accumulate less naked), never by more persistent execution.")
    out("")
    out("The REST of E = %d / %d = %.1f%% (mean PnL %s) — reachable in principle by execution."
        % (len(rest), len(e), _pct(len(rest), len(e)),
           ("%+.2f" % st.mean([x["pnl"] for x in rest])) if rest else "—"))
    out("")


def report_sell_recovery(recs, out):
    out("## (2) SELL_RECOVERY SHADOW BASELINE (case C: exit_branch == \"sold\")")
    out("")
    sold = [r for r in recs if r["exit_branch"] == "sold"]
    rows = []
    skipped = 0
    for r in sold:
        ref = r["mid_up_at_freeze"] if r["sell_side"] == "Up" else r["mid_dn_at_freeze"]
        if r["sell_px"] is None or ref is None or ref <= 0:
            skipped += 1
            continue
        rows.append((r, r["sell_px"] / ref, ref))
    out("Sold windows: %d.  Usable (sell_px and a positive ref mid at freeze): %d.  Skipped: %d."
        % (len(sold), len(rows), skipped))
    out("")
    out("recovery = sell_px / mid_at_freeze(sold side).")
    out("")
    if not rows:
        out("_No usable sold windows._")
        return

    def block(title, groups):
        out("| %s | n | mean recovery | p10 | p50 | p90 | mean sell_px | mean ref_mid |" % title)
        out("|---|---|---|---|---|---|---|---|")
        for name, g in groups:
            if not g:
                out("| %s | 0 | — | — | — | — | — | — |" % name)
                continue
            rc = [x[1] for x in g]
            out("| %s | %d | %.3f | %.3f | %.3f | %.3f | %.3f | %.3f |"
                % (name, len(g), st.mean(rc), _q(rc, 0.10), _q(rc, 0.50), _q(rc, 0.90),
                   st.mean([x[0]["sell_px"] for x in g]), st.mean([x[2] for x in g])))
        out("")

    block("regime", [(reg, [x for x in rows if x[0]["hindsight"] == reg]) for reg in REGIMES]
          + [("**ALL**", rows)])
    block("naked_at_freeze",
          [(lab, [x for x in rows if lo <= x[0]["naked_at_freeze"] < hi])
           for lab, lo, hi in NAKED_BUCKETS] + [("**ALL**", rows)])

    out("### HEADLINE")
    out("")
    for reg in REGIMES:
        g = [x[1] for x in rows if x[0]["hindsight"] == reg]
        if g:
            out("- **%s**: the sell recovers **%.1f%%** of the leg's freeze-time value (n=%d, median %.1f%%)."
                % (reg, 100 * st.mean(g), len(g), 100 * _q(g, 0.50)))
    allr = [x[1] for x in rows]
    out("- **ALL**: **%.1f%%** (n=%d, median %.1f%%)." % (100 * st.mean(allr), len(allr),
                                                          100 * _q(allr, 0.50)))
    out("")
    out("**THIS IS AN UPPER BOUND.** The shadow ALWAYS executes the sell whenever a bid exists; live")
    out("can have the FOK killed, so the live `sell_px_avg` / `mid_at_freeze` recovery will be WORSE")
    out("than every number in this table. A live figure BELOW these is expected, not a regression;")
    out("a live figure at or above them would mean the shadow is mismeasuring.")
    out("")


def main():
    paths = sys.argv[1:]
    if not paths:
        print("usage: _exec_baseline.py <book_jsonl> [more...]", file=sys.stderr)
        return 2
    try:
        head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=os.path.dirname(os.path.abspath(__file__)),
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        head = "unknown"

    recs = collect(paths)

    lines = []

    def out(s=""):
        lines.append(s)

    out("# Execution baseline — E-subdivision + sell_recovery shadow (OFFLINE, recorded tapes)")
    out("")
    out("commit: `%s`" % head)
    out("")
    out("frozen config: `%s` (cap/size/link_margin at the function defaults 6/5/0.01 = the live values)"
        % repr(CFG))
    out("")
    out("tapes (%d): %s" % (len(paths), ", ".join(p.split("/")[-1] for p in paths)))
    out("")
    out("Shadow sim on RECORDED tapes. No live trading, no orders, no config change. These are")
    out("BASELINES for a future live comparison — nothing here is tuned.")
    out("")
    report_e_subdivision(recs, out)
    report_sell_recovery(recs, out)

    text = "\n".join(lines)
    print(text)
    dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_exec_baseline_report.md")
    with open(dest, "w") as f:
        f.write(text + "\n")
    print("\n[written] %s" % dest, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
