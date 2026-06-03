"""Tests for phase-15 one-sided late-window favorite-buying compute_ladder."""

from quoter.config import Config
from quoter.strategy.ladder import compute_ladder, _certainty_size

# 5m window: window_frac = (300 - tte) / 300. tte=120 → frac 0.60 (late enough).
LATE_TTE = 120.0


def test_picks_higher_side_as_favorite():
    yes = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE)
    assert yes and all(q.side == "YES" for q in yes)
    no = compute_ladder(Config(), mid_yes=0.30, time_to_expiry=LATE_TTE)
    assert no and all(q.side == "NO" for q in no)


def test_dead_zone_no_quotes():
    assert compute_ladder(Config(), mid_yes=0.50, time_to_expiry=LATE_TTE) == []
    assert compute_ladder(Config(), mid_yes=0.52, time_to_expiry=LATE_TTE) == []


def test_too_early_no_quotes():
    # tte=290 → window_frac = (300-290)/300 = 0.033 < entry_start_frac 0.30
    assert compute_ladder(Config(), mid_yes=0.70, time_to_expiry=290.0) == []


def test_below_min_price_no_quotes():
    # favorite price 0.53 < favorite_min_price 0.55
    assert compute_ladder(Config(), mid_yes=0.53, time_to_expiry=LATE_TTE) == []


def test_caps_at_max_entry_price():
    q = compute_ladder(Config(), mid_yes=0.96, time_to_expiry=LATE_TTE)
    assert q and max(x.price for x in q) <= Config().max_entry_price


def test_falling_favorite_suppressed():
    # YES favorite price fell 0.75 → 0.70 (drop 0.05 > rise_tolerance 0.01)
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE, prev_mid_yes=0.75)
    assert out == []


def test_rising_favorite_allowed():
    # YES favorite price rose 0.65 → 0.70 → quotes
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE, prev_mid_yes=0.65)
    assert out


def test_velocity_disagree_blocks():
    # YES favorite but BTC velocity negative (down) → blocked
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE, velocity_short=-0.01)
    assert out == []


def test_velocity_agree_allowed():
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE, velocity_short=0.01)
    assert out


def test_velocity_none_falls_back():
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE, velocity_short=None)
    assert out  # not blocked when velocity unavailable (backtest)


def test_certainty_size_monotonic():
    cfg = Config()
    low = _certainty_size(0.60, 0.40, cfg)
    high = _certainty_size(0.90, 0.95, cfg)
    assert high > low


def test_per_market_cap_stops_adds():
    # large existing favorite inventory → spent proxy exceeds cap → []
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=1000)
    assert out == []


def test_only_favorite_side():
    out = compute_ladder(Config(), mid_yes=0.80, time_to_expiry=LATE_TTE)
    assert out and len({q.side for q in out}) == 1


def test_legacy_kwargs_accepted():
    # quoter_loop still passes committed_side / velocity_long; must not error.
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE,
                         committed_side="YES", velocity_long=0.0, timeframe="5m",
                         asset="BTC")
    assert out
