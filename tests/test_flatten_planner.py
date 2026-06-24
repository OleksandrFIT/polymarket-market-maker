from quoter.runner.flatten_planner import plan_naked_action, NakedAction


def test_below_cap_returns_none():
    assert plan_naked_action(8, 5, 0.5, 0.5, 0.5, 0.5, naked_cap=5) is None  # naked 3 < 5


def test_complete_when_other_side_cheap_yes_heavy():
    # naked +5 (YES heavy), held YES avg 0.61, NO ask 0.36 -> pair 0.97 < 1 -> COMPLETE NO
    a = plan_naked_action(10, 5, 0.61, None, 0.99, 0.36, naked_cap=5)
    assert a == NakedAction(kind="COMPLETE", side="NO", qty=5)


def test_complete_when_other_side_cheap_no_heavy():
    # naked -5 (NO heavy), held NO avg 0.40, YES ask 0.30 -> pair 0.70 < 1 -> COMPLETE YES
    a = plan_naked_action(5, 10, None, 0.40, 0.30, 0.99, naked_cap=5)
    assert a == NakedAction(kind="COMPLETE", side="YES", qty=5)


def test_sell_when_pair_would_exceed_one():
    # naked +5, YES avg 0.61, NO ask 0.45 -> pair 1.06 >= 1 -> SELL the heavy YES
    a = plan_naked_action(10, 5, 0.61, None, 0.99, 0.45, naked_cap=5)
    assert a == NakedAction(kind="SELL", side="YES", qty=5)


def test_sell_fallback_when_ask_missing():
    # no NO ask available -> cannot complete -> SELL heavy YES
    a = plan_naked_action(10, 5, 0.61, None, 0.99, None, naked_cap=5)
    assert a == NakedAction(kind="SELL", side="YES", qty=5)


def test_sell_when_heavy_avg_unknown():
    # heavy avg None (shouldn't happen, but be safe) -> cannot price completion -> SELL
    a = plan_naked_action(10, 5, None, None, 0.99, 0.10, naked_cap=5)
    assert a == NakedAction(kind="SELL", side="YES", qty=5)


from quoter.config import Config
from quoter.runner.flatten_planner import naked_action_due


def _cfg(**kw):
    return Config(**kw)


def test_due_false_when_flat():
    c = _cfg(complete_pairs=True, complete_gate_sec=60.0)
    assert naked_action_due(c, naked=0, time_remaining=10.0, naked_since_heavy=None, now=0.0) is False


def test_complete_pairs_due_only_in_late_window():
    c = _cfg(complete_pairs=True, complete_gate_sec=60.0)
    assert naked_action_due(c, naked=5, time_remaining=120.0, naked_since_heavy=None, now=0.0) is False
    assert naked_action_due(c, naked=5, time_remaining=45.0, naked_since_heavy=None, now=0.0) is True


def test_complete_continuous_due_throughout_window():
    c = _cfg(complete_pairs=True, complete_continuous=True, complete_gate_sec=120.0)
    # fires EARLY (not just last 120s) whenever naked != 0
    assert naked_action_due(c, naked=5, time_remaining=800.0, naked_since_heavy=None, now=0.0) is True
    assert naked_action_due(c, naked=5, time_remaining=45.0, naked_since_heavy=None, now=0.0) is True
    assert naked_action_due(c, naked=0, time_remaining=800.0, naked_since_heavy=None, now=0.0) is False


def test_recent_complete_qty_only_counts_within_lag():
    from quoter.runner.flatten_planner import recent_complete_qty
    log = [(0.0, 10.0), (5.0, 5.0), (18.0, 7.0)]
    # at now=20, grace=12 -> only completes newer than t=8 count (the 18.0 one)
    assert recent_complete_qty(log, now=20.0, grace=12.0) == 7.0
    # at now=8, grace=12 -> 0.0 aged out (8-0=8<12 still in), 5.0 and... only past entries
    assert recent_complete_qty([(0.0, 10.0), (5.0, 5.0)], now=8.0, grace=12.0) == 15.0
    # at now=14: 0.0 aged out (14-0=14>=12), 5.0 in (9<12)
    assert recent_complete_qty([(0.0, 10.0), (5.0, 5.0)], now=14.0, grace=12.0) == 5.0
    assert recent_complete_qty([], now=6.0, grace=12.0) == 0.0


def test_legacy_auto_flat_unchanged():
    c = _cfg(auto_flat=True, complete_pairs=False, naked_cap=5, flatten_grace_sec=20.0)
    assert naked_action_due(c, naked=3, time_remaining=200.0, naked_since_heavy=10.0, now=15.0) is False
    assert naked_action_due(c, naked=5, time_remaining=200.0, naked_since_heavy=10.0, now=15.0) is False
    assert naked_action_due(c, naked=5, time_remaining=200.0, naked_since_heavy=10.0, now=40.0) is True
    assert naked_action_due(c, naked=5, time_remaining=10.0, naked_since_heavy=10.0, now=15.0) is True


def test_auto_flat_at_cap_but_no_since_is_false():
    c = Config(auto_flat=True, complete_pairs=False, naked_cap=5, flatten_grace_sec=20.0)
    assert naked_action_due(c, naked=5, time_remaining=200.0, naked_since_heavy=None, now=100.0) is False


def test_both_off_never_due():
    c = _cfg(auto_flat=False, complete_pairs=False)
    assert naked_action_due(c, naked=10, time_remaining=5.0, naked_since_heavy=0.0, now=100.0) is False


def test_complete_cap_qty_bounds_cumulative():
    from quoter.runner.flatten_planner import complete_cap_qty
    assert complete_cap_qty(10, 0, 10) == 10      # fresh -> full
    assert complete_cap_qty(10, 6, 10) == 4       # 6 already done -> only 4 more
    assert complete_cap_qty(10, 10, 10) == 0      # at cap -> nothing
    assert complete_cap_qty(5, 8, 10) == 2        # near cap -> partial
    assert complete_cap_qty(5, 0, 10) == 5        # under cap -> full request
    assert complete_cap_qty(10, 12, 10) == 0      # already over -> 0, never negative


def test_balance_complete_qty_no_excess():
    from quoter.runner.flatten_planner import balance_complete_qty
    assert balance_complete_qty(5, 0) == 5     # naked 5, nothing done -> complete 5
    assert balance_complete_qty(5, 5) == 0     # already completed 5 (lag) -> stop
    assert balance_complete_qty(8, 5) == 3     # naked grew to 8 -> 3 more
    assert balance_complete_qty(5, 8) == 0     # over-done -> 0, never negative
    assert balance_complete_qty(0, 0) == 0
