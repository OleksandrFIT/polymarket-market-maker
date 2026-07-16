"""ENDGAME PROBABILITY — the single number that decides a future endgame-maker branch.

Read-only over recorded tapes. For each BTC 5m window, take the FAVOURITE's price at T-20 / T-15 /
T-10 seconds and ask how often the favourite actually resolved as the winner.

Why it matters: a maker bid on the favourite late in the window is +EV only if the REAL win
probability is materially ABOVE the price you pay. If P(win) merely tracks the price (market
efficient), the branch is dead and closes forever on one number. This script produces that number
and nothing else — it does NOT build the branch.

Method (strictly causal per window): favourite = the side priced > 0.5 at that instant
(Up price = up_mid; Down price = 1 - up_mid). Compare against `winner` from load_window.
Reported per price bucket: n, P(favourite wins), 95% Wilson CI. Buckets with n<30 are UNRELIABLE.

Run: POLY_MM_CACHE=... .venv/bin/python scripts/_endgame_prob.py <book_jsonl> [more...]
"""
import sys
import os
import math
import importlib.util
import collections

from quoter.research.mm_tape import load_window
from quoter.research.exec_ab import _mid

_SPEC = importlib.util.spec_from_file_location(
    "_config_grid", os.path.join(os.path.dirname(__file__), "_config_grid.py"))
_cg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cg)
parse_file = _cg.parse_file

OFFSETS = [20, 15, 10]                       # seconds before close (rel_ts = 300 - offset)
BUCKETS = [(0.85, 0.88), (0.88, 0.90), (0.90, 0.92), (0.92, 0.95), (0.95, 0.98)]
HEADLINE = (0.88, 0.95)                      # the band a late maker bid would sit in


def wilson(k, n, z=1.96):
    """95% Wilson score interval for a binomial proportion (better than normal approx at small n)."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (centre - half, centre + half)


def fav_at(mid_path, rel_ts):
    """Favourite side + its price at `rel_ts`, using the LAST mid at or before that time (causal).
    Returns (side, price) or None if no mid exists by then / the market sits exactly at 0.5."""
    prior = [m for (t, m) in mid_path if t <= rel_ts]
    if not prior:
        return None
    up = prior[-1]
    if up > 0.5:
        return ("Up", up)
    if up < 0.5:
        return ("Down", 1.0 - up)
    return None                              # dead heat -> no favourite


def main():
    # obs[offset] -> list of (fav_price, fav_won)
    obs = collections.defaultdict(list)
    n_windows = 0
    for path in sys.argv[1:]:
        snaps = parse_file(path)
        for slug, sn in snaps.items():
            w = load_window(slug)
            if not w or not w[0]:
                continue
            _tape, winner, _ = w
            open_ts = int(slug.rsplit("-", 1)[1])
            mid_path = []
            for s in sn:
                m = _mid(s["yes"])
                if m is not None:
                    mid_path.append((s["ts"] - open_ts, m))
            if not mid_path:
                continue
            n_windows += 1
            for off in OFFSETS:
                f = fav_at(mid_path, 300 - off)
                if f is None:
                    continue
                side, px = f
                obs[off].append((px, side == winner))
        snaps = None
        print("  ...%s -> %d windows" % (path.split("/")[-1], n_windows), file=sys.stderr)

    out = []

    def p(s=""):
        print(s)
        out.append(s)

    p("# Endgame probability — P(favourite wins | price, T-t)")
    p()
    p("OFFLINE, read-only over recorded tapes. %d windows." % n_windows)
    p("Favourite = side priced > 0.5 at that instant (causal: last mid at or before T-t).")
    p("A late MAKER bid on the favourite is +EV only if P(win) is materially ABOVE the price paid.")
    p("If P(win) tracks the price, the market is efficient there and the branch is DEAD.")
    p()
    for off in OFFSETS:
        rows = obs[off]
        p("## T-%ds  (n=%d observations)" % (off, len(rows)))
        p("%-14s %6s %9s %9s %-22s %s" % ("price bucket", "n", "P(win)", "mid-price", "95% Wilson CI", "verdict"))
        for lo, hi in BUCKETS:
            sel = [won for (px, won) in rows if lo <= px < hi]
            n = len(sel)
            if n == 0:
                p("%-14s %6d %9s %9s %-22s %s" % ("[%.2f,%.2f)" % (lo, hi), 0, "-", "-", "-", "no data"))
                continue
            k = sum(sel)
            pw = k / n
            ci = wilson(k, n)
            mid_px = (lo + hi) / 2.0
            # is the win rate ABOVE the price you'd pay (the whole bucket), with the CI clear of it?
            if n < 30:
                verdict = "UNRELIABLE (n<30)"
            elif ci[0] > hi:
                verdict = "ABOVE price -> +EV"
            elif ci[1] < lo:
                verdict = "BELOW price -> -EV"
            else:
                verdict = "tracks price (efficient)"
            p("%-14s %6d %9.4f %9.3f [%.4f, %.4f]  %s"
              % ("[%.2f,%.2f)" % (lo, hi), n, pw, mid_px, ci[0], ci[1], verdict))
        # headline band
        band = [won for (px, won) in rows if HEADLINE[0] <= px < HEADLINE[1]]
        if band:
            k, n = sum(band), len(band)
            pw = k / n
            ci = wilson(k, n)
            p()
            p("  HEADLINE  P(win | price in [%.2f,%.2f)) at T-%ds = %.4f  (n=%d, 95%% CI [%.4f, %.4f])"
              % (HEADLINE[0], HEADLINE[1], off, pw, n, ci[0], ci[1]))
            p("            a maker bid inside this band pays ~%.2f-%.2f; %s"
              % (HEADLINE[0], HEADLINE[1],
                 "P(win) CI sits ABOVE the band -> branch worth building"
                 if ci[0] > HEADLINE[1] else
                 ("P(win) CI sits BELOW the band -> branch is -EV, close it"
                  if ci[1] < HEADLINE[0] else
                  "P(win) overlaps the band -> market efficient here -> branch DEAD")))
        p()
    p("NOTE: this is the resolution probability only. It ignores fill probability (a late maker bid")
    p("may simply not fill) and adverse selection (it fills exactly when it is about to be wrong).")
    p("So it is an UPPER bound on the branch's value: if the number is not clearly above the price,")
    p("the branch cannot be rescued by execution.")

    rp = os.path.join(os.path.dirname(__file__), "_endgame_prob_report.md")
    with open(rp, "w") as f:
        f.write("\n".join(out) + "\n")
    print("\n(report written to %s)" % rp, file=sys.stderr)


if __name__ == "__main__":
    main()
