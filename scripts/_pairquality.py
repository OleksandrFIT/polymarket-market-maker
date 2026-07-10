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
        print("pair_cost: mean %.4f | median %.4f | %% windows < $1: %.0f%%" % (
            s["mean_pair_cost"], s["median_pair_cost"], 100 * s["pct_sub_dollar"]))
    print("match:naked median: %s | outcomes: %s | completes %d | sells %d" % (
        ("%.1f" % s["median_match_naked"]) if s["median_match_naked"] is not None else "n/a",
        s["outcomes"], s["completes"], s["sells"]))
    if s["n_priced"]:
        ok = s["mean_pair_cost"] <= 0.99 and s["pct_sub_dollar"] > 0.70
        print("VERDICT: %s (criterion: mean <= 0.99 AND >70%% windows < $1)" % (
            "linked-pair HOLDS live -> scale is the only lever" if ok
            else "pair >= $1 -> async/adverse beats execution; top_book ceiling confirmed"))


if __name__ == "__main__":
    main()
