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


def test_instrumentation_keys_present_and_sane():
    # Additive audit keys must exist, not change the tuple shape, and be internally consistent.
    rec, _ = _cg.top_book_window_gated(SNAPS, TAPE, "Up", SLUG, freeze_sec=30.0)
    assert set(("naked_at_freeze", "max_pair_cost", "exit_branch")) <= set(rec)
    # single mid-window snap, both legs merge -> ends flat, never reaches freeze -> clean branch.
    assert rec["naked_at_freeze"] == 0.0
    assert rec["exit_branch"] == "clean"
    # max observed per-merge pair cost = 0.501 + 0.451 = 0.952, and must obey the linked-pair cap (<$1).
    assert abs(rec["max_pair_cost"] - 0.952) < 1e-9
    assert rec["max_pair_cost"] < 1.0


def test_exit_branch_rode_when_naked_rides_to_resolution():
    # A single naked Up leg (only the Up SELL prints), no Down leg, no near-end completion snap ->
    # naked rides to resolution. Up wins -> rode_won.
    snaps = [_snap(OPEN + 250, 0.50, 0.55, 0.45, 0.50)]
    tape = [{"oi": 0, "side": "SELL", "price": 0.50, "size": 5.0, "ts": OPEN + 250}]
    rec, _ = _cg.top_book_window_gated(snaps, tape, "Up", SLUG, freeze_sec=30.0)
    assert rec["naked_at_freeze"] >= 1
    assert rec["exit_branch"] in ("rode_won", "rode_lost")
    assert rec["exit_branch"] == "rode_won"        # winner=="Up" and residual is Up
    # the tape's only snap is at 50s-to-close with freeze 30 -> the near-end block never ran at all
    assert rec["e_reason"] == "no_near_end_snap"


# --- e_reason subdivision of the RODE (E) tail ----------------------------------------------------
# NOTE: the sim does not model FOK-kill, so every e_reason is STRUCTURAL (the near-end price
# CONDITION was never met), not "an order was tried and killed".

def test_e_reason_none_and_freeze_mids_none_when_window_never_closes():
    rec, _ = _cg.top_book_window_gated(SNAPS, TAPE, "Up", SLUG, freeze_sec=30.0)
    assert rec["exit_branch"] == "clean"
    assert rec["e_reason"] is None                 # only rode_* windows carry a reason
    assert rec["mid_up_at_freeze"] is None         # never went closing -> no freeze snapshot
    assert rec["mid_dn_at_freeze"] is None
    assert rec["sell_px"] is None and rec["sell_side"] is None


def test_e_reason_no_bid_on_loser_and_freeze_mids_captured():
    # rel 200: only the Up SELL prints -> naked Up 5. rel 280 (inside near-end at freeze 60): the
    # Down book has NO ask (completion impossible) and the heavy Up book has NO bid -> nothing to
    # sell into -> structurally unfixable.
    snaps = [_snap(OPEN + 200, 0.50, 0.55, 0.45, 0.50),
             {"ts": OPEN + 280,
              "yes": {"bids": [], "asks": [["0.55", "500"]]},
              "no": {"bids": [["0.45", "500"]], "asks": []}}]
    tape = [{"oi": 0, "side": "SELL", "price": 0.50, "size": 5.0, "ts": OPEN + 200}]
    rec, _ = _cg.top_book_window_gated(snaps, tape, "Up", SLUG, freeze_sec=60.0)
    assert rec["exit_branch"] == "rode_won"
    assert rec["e_reason"] == "no_bid_on_loser"
    assert rec["sell_px"] is None                  # no bid -> no sell happened
    # freeze fires at the rel-280 snap (first tick with <=60s left); mids are captured THERE.
    # _mid falls back to the single present side when the book is one-sided (ask-only Up -> 0.55).
    assert abs(rec["mid_up_at_freeze"] - 0.55) < 1e-9
    assert abs(rec["mid_dn_at_freeze"] - 0.45) < 1e-9


def test_e_reason_budget_when_completion_priced_ok_but_blocked():
    # budget = pwc + complete_budget = 3.0. Up fills 5 @ 0.501 = 2.505 (fits). At rel 280 the Down ask
    # 0.40 makes the pair 0.901 < $1 so completion is PRICED fine, but 2.505 + 5*0.40 > 3.0 -> blocked.
    snaps = [_snap(OPEN + 200, 0.50, 0.55, 0.39, 0.50),
             _snap(OPEN + 280, 0.50, 0.55, 0.39, 0.40)]
    tape = [{"oi": 0, "side": "SELL", "price": 0.50, "size": 5.0, "ts": OPEN + 200}]
    rec, _ = _cg.top_book_window_gated(snaps, tape, "Up", SLUG, freeze_sec=60.0,
                                       pwc=3.0, complete_budget=0.0)
    assert rec["exit_branch"] == "rode_won"
    assert rec["completes"] == 0
    assert rec["e_reason"] == "budget"


def test_sell_px_and_side_recorded_on_the_sell_branch():
    # rel 200: naked Up 5 @ 0.501. rel 280: the Down ask is 0.99 -> completion condition fails, so the
    # sell branch runs and dumps the heavy Up leg into its 0.62 bid.
    snaps = [_snap(OPEN + 200, 0.50, 0.55, 0.45, 0.50),
             _snap(OPEN + 280, 0.62, 0.66, 0.34, 0.99)]
    tape = [{"oi": 0, "side": "SELL", "price": 0.50, "size": 5.0, "ts": OPEN + 200}]
    rec, _ = _cg.top_book_window_gated(snaps, tape, "Up", SLUG, freeze_sec=60.0)
    assert rec["exit_branch"] == "sold"
    assert rec["sells"] == 1
    assert rec["sell_side"] == "Up"
    assert abs(rec["sell_px"] - 0.62) < 1e-9       # the realized heavy best bid
    assert abs(rec["mid_up_at_freeze"] - 0.64) < 1e-9   # (0.62+0.66)/2 at the freeze tick
    assert rec["e_reason"] is None                 # not a rode window


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
