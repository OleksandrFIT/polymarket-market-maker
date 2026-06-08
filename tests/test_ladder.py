"""Tests for phase-18 momentum-entry compute_ladder (+ cheap-tail lottery)."""

from dataclasses import replace
from quoter.config import Config
from quoter.strategy.ladder import compute_ladder

LATE_TTE = 60.0
VUP = 0.01    # strong up velocity (>= momentum_velocity_threshold 0.001)
VDN = -0.01   # strong down velocity


def _favbids(out, cfg=None):
    cfg = cfg or Config()
    return [q for q in out if q.price >= cfg.momentum_min_price]


def test_no_velocity_no_momentum():
    out = compute_ladder(Config(), mid_yes=0.55, time_to_expiry=LATE_TTE, velocity_short=None)
    assert _favbids(out) == []


def test_weak_velocity_no_momentum():
    out = compute_ladder(Config(), mid_yes=0.55, time_to_expiry=LATE_TTE, velocity_short=0.0001)
    assert _favbids(out) == []


def test_momentum_buys_up_side():
    out = compute_ladder(Config(), mid_yes=0.55, time_to_expiry=LATE_TTE, velocity_short=VUP)
    fav = _favbids(out)
    assert fav and all(q.side == "YES" for q in fav)


def test_momentum_buys_down_side():
    out = compute_ladder(Config(), mid_yes=0.45, time_to_expiry=LATE_TTE, velocity_short=VDN)
    fav = _favbids(out)
    assert fav and all(q.side == "NO" for q in fav)


def test_momentum_skips_too_expensive():
    out = compute_ladder(Config(), mid_yes=0.80, time_to_expiry=LATE_TTE, velocity_short=VUP)
    assert _favbids(out) == []


def test_momentum_skips_too_cheap():
    out = compute_ladder(Config(), mid_yes=0.30, time_to_expiry=LATE_TTE, velocity_short=VUP)
    assert all(q.side != "YES" or q.price < Config().momentum_min_price for q in out)


def test_momentum_commit_one_side():
    out = compute_ladder(Config(), mid_yes=0.45, time_to_expiry=LATE_TTE, velocity_short=VDN,
                         inventory_yes_qty=40)
    assert not any(q.side == "NO" and q.price >= Config().momentum_min_price for q in out)


def test_momentum_cap_stops():
    out = compute_ladder(Config(), mid_yes=0.55, time_to_expiry=LATE_TTE, velocity_short=VUP,
                         inventory_yes_qty=2000)
    assert _favbids(out) == []


def test_momentum_flat_size():
    cfg = Config()
    out = compute_ladder(cfg, mid_yes=0.55, time_to_expiry=LATE_TTE, velocity_short=VUP)
    fav = _favbids(out, cfg)
    assert fav and all(q.size == cfg.flat_size for q in fav)


def test_lottery_still_fires():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE, velocity_short=None)
    assert any(q.side == "NO" and q.price <= Config().lottery_max_price for q in out)


def test_lottery_size_zero_disables():
    out = compute_ladder(replace(Config(), lottery_size=0), mid_yes=0.90,
                         time_to_expiry=LATE_TTE, velocity_short=None)
    assert out == []
