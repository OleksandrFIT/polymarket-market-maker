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
    """Distribution + verdict inputs. The decision metric is the REBATE-ADJUSTED effective pair
    cost: makers earn a rebate (2nd revenue), so break-even is ~$1.00 on `pair_cost_effective`,
    NOT $1.00 on raw `pair_cost`. Falls back to raw pair_cost for records that predate the field."""
    priced = [r["pair_cost"] for r in records if r.get("pair_cost") is not None]
    eff = [r.get("pair_cost_effective", r.get("pair_cost")) for r in records
           if r.get("pair_cost_effective", r.get("pair_cost")) is not None]
    outcomes = {}
    for r in records:
        outcomes[r.get("resid_outcome", "?")] = outcomes.get(r.get("resid_outcome", "?"), 0) + 1
    matched = [r["match_naked"] for r in records if r.get("match_naked") is not None]
    return {
        "n_windows": len(records),
        "n_priced": len(priced),
        "mean_pair_cost": (sum(priced) / len(priced)) if priced else None,
        "median_pair_cost": st.median(priced) if priced else None,
        "mean_pair_cost_eff": (sum(eff) / len(eff)) if eff else None,
        "pct_sub_dollar": (sum(1 for x in priced if x < 1.0) / len(priced)) if priced else None,
        "pct_eff_sub_dollar": (sum(1 for x in eff if x < 1.0) / len(eff)) if eff else None,
        "median_match_naked": st.median(matched) if matched else None,
        "rebate_total": sum(r.get("rebate_accrued", 0.0) for r in records),
        "outcomes": outcomes,
        "completes": sum(r.get("completes", 0) for r in records),
        "sells": sum(r.get("sells", 0) for r in records),
    }
