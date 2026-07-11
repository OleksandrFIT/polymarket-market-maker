"""Read a control.log (arg or stdin), aggregate topbook_fillquality, print the pair-cost verdict.
Usage: python3 scripts/_pairquality.py /path/to/control.log   (or: ... < control.log)"""
import sys

from quoter.research.pairquality import parse_lines, summarize


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            recs = parse_lines(f)
    else:
        recs = parse_lines(sys.stdin)
    s = summarize(recs)
    if not s["n_windows"]:
        print("no topbook_fillquality windows found")
        return
    print("windows: %d (priced %d)" % (s["n_windows"], s["n_priced"]))
    if s["n_priced"]:
        print("pair_cost RAW:  mean %.4f | median %.4f | %% < $1: %.0f%%" % (
            s["mean_pair_cost"], s["median_pair_cost"], 100 * s["pct_sub_dollar"]))
        eff = s.get("mean_pair_cost_eff")
        if eff is not None:
            print("pair_cost EFF (rebate-adjusted): mean %.4f | %% < $1: %.0f%% | rebate total $%.3f" % (
                eff, 100 * (s.get("pct_eff_sub_dollar") or 0), s.get("rebate_total", 0.0)))
    print("match:naked median: %s | outcomes: %s | completes %d | sells %d" % (
        ("%.1f" % s["median_match_naked"]) if s["median_match_naked"] is not None else "n/a",
        s["outcomes"], s["completes"], s["sells"]))
    if s["n_priced"]:
        eff = s.get("mean_pair_cost_eff") or s["mean_pair_cost"]
        peff = s.get("pct_eff_sub_dollar")
        peff = peff if peff is not None else s["pct_sub_dollar"]
        ok = eff <= 1.00 and peff > 0.50   # rebate-adjusted break-even ~$1.00, not raw 0.99
        print("VERDICT: %s (criterion: effective pair-cost <= $1.00 AND >50%% windows eff < $1)" % (
            "maker + rebate HOLDS live -> scale is the lever (rebate is linear in turnover)" if ok
            else "effective pair >= $1 -> adverse selection beats even the rebate; ceiling confirmed"))


if __name__ == "__main__":
    main()
