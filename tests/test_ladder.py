"""Tests for phase-16 full-replication compute_ladder (late + high + one-sided + flat)."""

from quoter.config import Config
from quoter.strategy.ladder import compute_ladder

LATE_TTE = 60.0  # 5m: window_frac = (300-60)/300 = 0.80 (>= entry_start_frac 0.60)


def test_picks_higher_side_as_favorite():
    yes = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE)
    assert yes and all(q.side == "YES" for q in yes)
    no = compute_ladder(Config(), mid_yes=0.10, time_to_expiry=LATE_TTE)
    assert no and all(q.side == "NO" for q in no)


def test_below_min_price_no_quotes():
    assert compute_ladder(Config(), mid_yes=0.80, time_to_expiry=LATE_TTE) == []


def test_too_early_no_quotes():
    assert compute_ladder(Config(), mid_yes=0.90, time_to_expiry=200.0) == []


def test_caps_at_max_entry_price():
    q = compute_ladder(Config(), mid_yes=0.985, time_to_expiry=LATE_TTE)
    assert q and max(x.price for x in q) <= Config().max_entry_price


def test_commit_one_side_holds_yes():
    out = compute_ladder(Config(), mid_yes=0.10, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=10)
    assert out == []


def test_commit_one_side_holds_no():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_no_qty=10)
    assert out == []


def test_commit_one_side_same_side_ok():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=10)
    assert out and all(q.side == "YES" for q in out)


def test_flat_size():
    cfg = Config()
    out = compute_ladder(cfg, mid_yes=0.90, time_to_expiry=LATE_TTE)
    assert out and all(q.size == cfg.flat_size for q in out)


def test_falling_favorite_suppressed():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE, prev_mid_yes=0.95)
    assert out == []


def test_velocity_disagree_blocks():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE, velocity_short=-0.01)
    assert out == []


def test_velocity_none_falls_back():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE, velocity_short=None)
    assert out


def test_only_favorite_side():
    out = compute_ladder(Config(), mid_yes=0.92, time_to_expiry=LATE_TTE)
    assert out and len({q.side for q in out}) == 1


def test_per_market_cap_stops_adds():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=1000)
    assert out == []
