from quoter.runner.tilt_planner import plan_tilt


def test_neutral_returns_zero():
    assert plan_tilt("NEUTRAL", 0.7, 0, 5.0, 15.0, 10) == 0.0


def test_bad_ask_returns_zero():
    assert plan_tilt("UP", None, 0, 5.0, 15.0, 10) == 0.0
    assert plan_tilt("UP", 0.0, 0, 5.0, 15.0, 10) == 0.0
    assert plan_tilt("UP", 1.0, 0, 5.0, 15.0, 10) == 0.0


def test_above_max_price_returns_zero():
    assert plan_tilt("UP", 0.95, 0, 5.0, 15.0, 10, max_price=0.90) == 0.0


def test_already_enough_returns_zero():
    # inv_fav > spent → gap < 0
    assert plan_tilt("UP", 0.7, 10, 5.0, 15.0, 10) == 0.0


def test_gap_exactly_zero_returns_zero():
    # inv_fav == spent → gap == 0 (boundary of the gap <= 0 guard)
    assert plan_tilt("UP", 0.7, 5, 5.0, 15.0, 10) == 0.0


def test_down_bias_sizes_symmetrically():
    # DOWN is treated identically to UP after the first guard
    assert plan_tilt("DOWN", 0.6, 0, 8.0, 100.0, 10) == 10.0


def test_sizes_toward_spent_capped_by_step():
    # gap = 8, q = 8/(1-0.6) = 20, capped by step 10
    assert plan_tilt("UP", 0.6, 0, 8.0, 100.0, 10) == 10.0


def test_budget_cap():
    # budget_left = 15 - 14 = 1 ; 1/0.5 = 2 shares (below step 10)
    assert plan_tilt("UP", 0.5, 0, 14.0, 15.0, 10) == 2.0


def test_budget_exhausted_returns_zero():
    assert plan_tilt("UP", 0.6, 0, 15.0, 15.0, 10) == 0.0
