"""Plumbing smoke test for the offline chop-gate grid-search (scripts/_config_grid.py).
Not an EV assertion: verifies the gated shadow window returns a valid (rec, rebate) tuple, earns a
non-negative rebate on a maker fill, and that an early-closing (HIGH freeze_sec) accumulates no more
pairs than a late-closing (LOW freeze_sec) on a constructed mid-window fill; plus that two different
chop_lookback_sec values both run and return valid records."""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "_config_grid",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "_config_grid.py"))
_cg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cg)


def _snap(ts, ub, ua, db, da):
    return {"ts": ts,
            "yes": {"bids": [[f"{ub:.3f}", "500"]], "asks": [[f"{ua:.3f}", "500"]]},
            "no": {"bids": [[f"{db:.3f}", "500"]], "asks": [[f"{da:.3f}", "500"]]}}


# Mid-window snap at rel_ts=250s: our Up bid 0.501 & Down bid 0.451, SELL prints at/below each ->
# both maker legs fill 5, merge 5. At freeze<50s left this snap is still accumulating.
SLUG = "btc-updown-5m-1000000000"
OPEN = 1000000000
SNAPS = [_snap(OPEN + 250, 0.50, 0.55, 0.45, 0.50)]
TAPE = [{"oi": 0, "side": "SELL", "price": 0.50, "size": 5.0, "ts": OPEN + 250},
        {"oi": 1, "side": "SELL", "price": 0.45, "size": 5.0, "ts": OPEN + 250}]


def test_returns_rec_and_nonneg_rebate():
    rec, rebate = _cg.top_book_window_gated(SNAPS, TAPE, "Up", SLUG, freeze_sec=30.0)
    assert rec["style"] == "top_book_gated"
    assert rebate >= 0.0
    # both maker legs filled -> a merged pair and a strictly positive rebate
    assert rec["pairs_merged"] == 5.0
    assert rebate > 0.0
    assert abs(rec["pair_cost"] - 0.952) < 1e-9


def test_early_close_accumulates_no_more():
    # freeze=60 -> the rel_ts=250 snap (50s left) is CLOSING -> no new accumulation.
    # freeze=30 -> the same snap (50s left) is still accumulating -> merges a pair.
    late, _ = _cg.top_book_window_gated(SNAPS, TAPE, "Up", SLUG, freeze_sec=30.0)
    early, _ = _cg.top_book_window_gated(SNAPS, TAPE, "Up", SLUG, freeze_sec=60.0)
    assert early["pairs_merged"] <= late["pairs_merged"]
    assert early["pairs_merged"] == 0.0
    assert late["pairs_merged"] == 5.0


def test_lookback_values_both_run():
    for lb in (40.0, 80.0):
        rec, rebate = _cg.top_book_window_gated(SNAPS, TAPE, "Up", SLUG, chop_lookback_sec=lb)
        assert rec["style"] == "top_book_gated"
        assert rec["pairs_merged"] >= 0.0
        assert rebate >= 0.0


def test_grid_includes_defaults():
    configs = [(s, f, lb) for s in _cg.GRID_SHIFT for f in _cg.GRID_FREEZE for lb in _cg.GRID_LOOKBACK]
    assert len(configs) == 27
    assert (0.02, 45.0, 60.0) in configs
