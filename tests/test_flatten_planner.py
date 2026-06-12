from quoter.runner.flatten_planner import plan_flatten, FlattenDecision


def test_below_cap_returns_none():
    assert plan_flatten(inv_yes=8, inv_no=5, naked_cap=5) is None  # naked 3 < 5


def test_at_cap_yes_heavy_sells_yes_excess():
    d = plan_flatten(inv_yes=10, inv_no=5, naked_cap=5)  # naked +5
    assert d == FlattenDecision(side="YES", qty=5)


def test_no_heavy_sells_no_excess():
    d = plan_flatten(inv_yes=5, inv_no=15, naked_cap=5)  # naked -10
    assert d == FlattenDecision(side="NO", qty=10)


def test_qty_equals_naked_leaves_pairs_intact():
    d = plan_flatten(inv_yes=12, inv_no=4, naked_cap=5)  # naked +8
    assert d.side == "YES" and d.qty == 8  # selling 8 leaves 4 YES / 4 NO = 4 pairs


def test_balanced_returns_none():
    assert plan_flatten(inv_yes=5, inv_no=5, naked_cap=5) is None
