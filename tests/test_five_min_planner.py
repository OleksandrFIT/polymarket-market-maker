from quoter.runner.five_min_planner import plan_five_min


def test_accumulate_before_min2_leans_into_leader():
    p = plan_five_min(1.0, None, None, 0.0, 0.0, 15.0, 3, 0.62, 0.78, 5, 0.55, 0.45)
    assert p.passed is None
    assert ("YES", 0.55, 15.0) in p.orders     # leader YES: rung_size*lean = 15
    assert ("NO", 0.45, 5.0) in p.orders        # laggard: rung_size = 5


def test_min2_pass_continues():
    p = plan_five_min(2.0, "YES", "YES", 0.70, 0.0, 15.0, 3, 0.62, 0.78, 5, 0.70, 0.30)
    assert p.passed is True
    assert ("YES", 0.70, 15.0) in p.orders


def test_min2_fail_inconsistent_leader():
    p = plan_five_min(2.0, "YES", "NO", 0.70, 0.0, 15.0, 3, 0.62, 0.78, 5, 0.70, 0.30)
    assert p.passed is False
    assert p.orders == []


def test_min2_fail_out_of_band():
    p = plan_five_min(2.0, "YES", "YES", 0.85, 0.0, 15.0, 3, 0.62, 0.78, 5, 0.85, 0.15)
    assert p.passed is False
    assert p.orders == []


def test_budget_exhausted_returns_empty():
    p = plan_five_min(1.0, None, None, 0.0, 15.0, 15.0, 3, 0.62, 0.78, 5, 0.55, 0.45)
    assert p.orders == []


def test_leader_is_the_higher_mid():
    p = plan_five_min(1.0, None, None, 0.0, 0.0, 15.0, 3, 0.62, 0.78, 5, 0.40, 0.60)
    assert ("NO", 0.60, 15.0) in p.orders        # NO is the leader (higher mid)
