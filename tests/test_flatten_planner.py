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
