"""Aggregate topbook_fillquality log records into the decision-grade pair-cost verdict."""
from quoter.research.pairquality import parse_lines, summarize


def _line(pair_cost, naked_resid, resid_outcome, pairs=20.0):
    import json
    return json.dumps({
        "event": "topbook_fillquality", "slug": "btc-updown-5m-1",
        "pair_cost": pair_cost, "pairs_merged": pairs, "naked_resid": naked_resid,
        "resid_outcome": resid_outcome, "match_naked": None, "completes": 1,
        "sells": 0, "spent": 10.0, "level": "info",
    })


def test_parse_ignores_non_fillquality_and_garbage():
    lines = [
        '{"event": "topbook_done", "slug": "x"}',
        "not json at all",
        _line(0.95, 0.0, "flat"),
    ]
    recs = parse_lines(lines)
    assert len(recs) == 1
    assert recs[0]["pair_cost"] == 0.95


def test_summarize_computes_pct_sub_dollar_and_outcomes():
    recs = parse_lines([
        _line(0.95, 0.0, "flat"),
        _line(0.98, 0.0, "flat"),
        _line(1.04, 5.0, "LOST"),
        _line(None, 5.0, "WON"),      # no pairs -> excluded from pair_cost stats
    ])
    s = summarize(recs)
    assert s["n_windows"] == 4
    assert s["n_priced"] == 3                       # windows with a pair_cost
    assert abs(s["mean_pair_cost"] - (0.95 + 0.98 + 1.04) / 3) < 1e-9
    assert abs(s["pct_sub_dollar"] - 2 / 3) < 1e-9  # 0.95, 0.98 < 1.0
    assert s["outcomes"] == {"flat": 2, "LOST": 1, "WON": 1}
