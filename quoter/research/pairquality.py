"""Pure aggregation of `topbook_fillquality` log records -> pair-cost verdict.
The decision metric: what fraction of windows assembled a matched pair < $1 (linked-pair
holds live) vs >= $1 (async/adverse still beats our execution). No I/O here."""
import json
import statistics as st


def parse_lines(lines):
    """Return the parsed topbook_fillquality records from an iterable of log lines."""
    out = []
    for line in lines:
        line = line.strip()
        if '"topbook_fillquality"' not in line:
            continue
        try:
            r = json.loads(line)
        except (ValueError, TypeError):
            continue
        if r.get("event") == "topbook_fillquality":
            out.append(r)
    return out


def summarize(records):
    """Distribution + verdict inputs across fillquality records."""
    priced = [r["pair_cost"] for r in records if r.get("pair_cost") is not None]
    outcomes = {}
    for r in records:
        outcomes[r.get("resid_outcome", "?")] = outcomes.get(r.get("resid_outcome", "?"), 0) + 1
    matched = [r["match_naked"] for r in records if r.get("match_naked") is not None]
    return {
        "n_windows": len(records),
        "n_priced": len(priced),
        "mean_pair_cost": (sum(priced) / len(priced)) if priced else None,
        "median_pair_cost": st.median(priced) if priced else None,
        "pct_sub_dollar": (sum(1 for x in priced if x < 1.0) / len(priced)) if priced else None,
        "median_match_naked": st.median(matched) if matched else None,
        "outcomes": outcomes,
        "completes": sum(r.get("completes", 0) for r in records),
        "sells": sum(r.get("sells", 0) for r in records),
    }
