"""Tests for phase-16/17 full-replication compute_ladder (late + high + one-sided + flat + lottery)."""

from dataclasses import replace

from quoter.config import Config
from quoter.strategy.ladder import compute_ladder

LATE_TTE = 60.0  # 5m: window_frac = (300-60)/300 = 0.80 (>= entry_start_frac 0.60)


def test_picks_higher_side_as_favorite():
    fmin = Config().favorite_min_price
    yes = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE)
    fav = [q for q in yes if q.price >= fmin]
    assert fav and all(q.side == "YES" for q in fav)
    no = compute_ladder(Config(), mid_yes=0.10, time_to_expiry=LATE_TTE)
    favn = [q for q in no if q.price >= fmin]
    assert favn and all(q.side == "NO" for q in favn)


def test_below_min_price_no_favorite_quotes():
    # mid=0.80 → favorite below min_price, but lottery NO at 0.20 still fires
    out = compute_ladder(Config(), mid_yes=0.80, time_to_expiry=LATE_TTE)
    assert all(q.side == "NO" for q in out)


def test_too_early_no_favorite_quotes():
    # tte=200 → window_frac too low for favorite; lottery still fires (exempt)
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=200.0)
    assert all(q.side == "NO" for q in out)


def test_caps_at_max_entry_price():
    q = compute_ladder(Config(), mid_yes=0.985, time_to_expiry=LATE_TTE)
    assert q and max(x.price for x in q) <= Config().max_entry_price


def test_commit_one_side_holds_yes():
    # inventory_yes=10 > inventory_no=0 → favorite NO blocked; YES lottery still fires
    out = compute_ladder(Config(), mid_yes=0.10, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=10)
    assert all(q.side == "YES" for q in out)


def test_commit_one_side_holds_no():
    # inventory_no=10 > inventory_yes=0 → favorite YES blocked; NO lottery still fires
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_no_qty=10)
    assert all(q.side == "NO" for q in out)


def test_commit_one_side_same_side_ok():
    fmin = Config().favorite_min_price
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=10)
    fav = [q for q in out if q.price >= fmin]
    assert fav and all(q.side == "YES" for q in fav)


def test_flat_size():
    # Favorite leg uses flat_size; lottery leg uses lottery_size. Check favorite quotes.
    cfg = Config()
    out = compute_ladder(cfg, mid_yes=0.90, time_to_expiry=LATE_TTE)
    fav_quotes = [q for q in out if q.side == "YES"]
    assert fav_quotes and all(q.size == cfg.flat_size for q in fav_quotes)


def test_falling_favorite_suppressed():
    # prev_mid=0.95 → falling favorite → favorite leg blocked; lottery still fires
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE, prev_mid_yes=0.95)
    assert all(q.side == "NO" for q in out)


def test_velocity_disagree_blocks():
    # velocity disagrees → favorite leg blocked; lottery still fires
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE, velocity_short=-0.01)
    assert all(q.side == "NO" for q in out)


def test_velocity_none_falls_back():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE, velocity_short=None)
    assert out


def test_lottery_adds_underdog_bids():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE)
    assert any(q.side == "YES" for q in out)
    assert any(q.side == "NO" for q in out)


def test_lottery_price_band():
    # mid 0.55 → underdog price 0.45 > lottery_max_price 0.40 → no NO lottery
    out = compute_ladder(Config(), mid_yes=0.55, time_to_expiry=LATE_TTE)
    assert all(q.side != "NO" for q in out)


def test_lottery_size_zero_disables():
    cfg = replace(Config(), lottery_size=0)
    out = compute_ladder(cfg, mid_yes=0.90, time_to_expiry=LATE_TTE)
    assert out and all(q.side == "YES" for q in out)


def test_lottery_cap_stops():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_no_qty=1000)
    assert all(q.side != "NO" for q in out)


def test_lottery_exempt_from_commit():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=50)
    assert any(q.side == "YES" for q in out)
    assert any(q.side == "NO" for q in out)


def test_lottery_exempt_from_entry_start():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=290.0)
    assert out and all(q.side == "NO" for q in out)


def test_lottery_flip_does_not_unseat_favorite():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=50, inventory_no_qty=30)
    assert any(q.side == "YES" for q in out)


def test_per_market_cap_stops_adds():
    # favorite cap hit → favorite leg blocked; NO lottery (udog_qty=0) still fires
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=1000)
    assert all(q.side == "NO" for q in out)
