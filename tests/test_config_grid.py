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


# --- SOFT vs HARD revocation ---------------------------------------------------------------------
# A window with a strong sustained up-lean (up-mid 0.80) that trend-CLOSES at ~110s, then a SELL
# print crosses BOTH frozen bids at 120s (a revert refilling standing bids). HARD cancelled the bids
# at close -> no fill; SOFT kept them -> fills both legs -> a merged pair. This is the whole point of
# soft revoke: a false-trend-revoke on a chop window is nearly free.
_SR_OPEN = 1000000000


def _lean_snap(rel):
    return {"ts": _SR_OPEN + rel,
            "yes": {"bids": [["0.79", "500"]], "asks": [["0.81", "500"]]},   # up-mid 0.80, dev 0.30
            "no": {"bids": [["0.20", "500"]], "asks": [["0.22", "500"]]}}


_SR_SNAPS = [_lean_snap(r) for r in (40, 100, 110, 120, 130)]
# revert prints at 120s (after the ~110s trend close): cross frozen Up 0.791 and Down 0.201.
_SR_TAPE = [{"oi": 0, "side": "SELL", "price": 0.79, "size": 5.0, "ts": _SR_OPEN + 120},
            {"oi": 1, "side": "SELL", "price": 0.20, "size": 5.0, "ts": _SR_OPEN + 120}]
_SR_KW = dict(chop_dev_thresh=0.28, chop_detect_sec=100.0, chop_confirm_sec=10.0,
              chop_lookback_sec=60.0, freeze_sec=45.0)


def test_trend_close_fires_and_records_causal_hindsight():
    rec, _ = _cg.top_book_window_gated(_SR_SNAPS, _SR_TAPE, "Up", "btc-updown-5m-1000000000",
                                       revoke_mode="hard", **_SR_KW)
    assert rec["closing_reason"] == "trend"       # sustained lean -> trend revoke (not clock)
    assert rec["hindsight"] in ("chop", "reversal", "trend")


def test_soft_revoke_refills_frozen_bids_hard_does_not():
    hard, _ = _cg.top_book_window_gated(_SR_SNAPS, _SR_TAPE, "Up", "btc-updown-5m-1000000000",
                                        revoke_mode="hard", **_SR_KW)
    soft, _ = _cg.top_book_window_gated(_SR_SNAPS, _SR_TAPE, "Up", "btc-updown-5m-1000000000",
                                        revoke_mode="soft", **_SR_KW)
    assert hard["pairs_merged"] == 0.0            # hard cancelled the bids at close -> no post-close fill
    assert soft["pairs_merged"] == 5.0            # soft kept them -> the revert refilled both legs
    assert soft["pairs_merged"] > hard["pairs_merged"]
