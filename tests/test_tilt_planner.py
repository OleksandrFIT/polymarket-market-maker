from quoter.runner.tilt_planner import plan_tilt


def test_neutral_returns_zero():
    assert plan_tilt("NEUTRAL", 0.7, 0.0, 5.0, 15.0, 0.65, 10) == 0.0


def test_bad_ask_returns_zero():
    assert plan_tilt("UP", None, 0.0, 5.0, 15.0, 0.65, 10) == 0.0
    assert plan_tilt("UP", 0.0, 0.0, 5.0, 15.0, 0.65, 10) == 0.0
    assert plan_tilt("UP", 1.0, 0.0, 5.0, 15.0, 0.65, 10) == 0.0


def test_above_max_price_returns_zero():
    assert plan_tilt("UP", 0.95, 0.0, 5.0, 15.0, 0.65, 10, max_price=0.90) == 0.0


def test_already_at_target_returns_zero():
    # fav_cost >= tilt_frac*cap (0.65*15=9.75) -> gap_usd <= 0
    assert plan_tilt("UP", 0.7, 9.75, 5.0, 15.0, 0.65, 10) == 0.0


def test_down_bias_sizes_symmetrically():
    # target 9.75, fav_cost 0, ask 0.6 -> q=16.25 capped by step 10
    assert plan_tilt("DOWN", 0.6, 0.0, 2.0, 100.0, 0.65, 10) == 10.0


def test_sizes_toward_target_capped_by_step():
    # target 9.75, gap 9.75, q=16.25 -> step 10
    assert plan_tilt("UP", 0.6, 0.0, 2.0, 100.0, 0.65, 10) == 10.0


def test_budget_cap():
    # spent 14, cap 15 -> budget_left 1; 1/0.5 = 2 shares (below step 10)
    assert plan_tilt("UP", 0.5, 0.0, 14.0, 15.0, 0.65, 10) == 2.0


def test_budget_exhausted_returns_zero():
    assert plan_tilt("UP", 0.6, 0.0, 15.0, 15.0, 0.65, 10) == 0.0
