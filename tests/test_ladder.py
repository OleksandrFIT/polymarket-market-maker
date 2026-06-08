"""Phase-19 two-sided merge-maker tests."""

from quoter.config import Config
from quoter.strategy.ladder import compute_ladder


def cfg(**kw):
    base = dict(
        merge_edge=0.01,
        max_naked_shares=20,
        merge_levels=2,
        flat_size=10,
        per_market_cap_usd=50.0,
        min_time_to_expiry_sec=5.0,
    )
    base.update(kw)
    return Config(**base)


def best(bids, side):
    ps = [b.price for b in bids if b.side == side]
    return max(ps) if ps else None


def test_posts_both_legs():
    b = compute_ladder(cfg(), mid_yes=0.55, time_to_expiry=60)
    assert best(b, "YES") is not None
    assert best(b, "NO") is not None


def test_pair_below_one():
    b = compute_ladder(cfg(), mid_yes=0.55, time_to_expiry=60)
    assert best(b, "YES") + best(b, "NO") < 1.0


def test_no_edge_skips():
    # With merge_edge=0 the pair rounds to exactly $1.00 -> no edge -> empty.
    b = compute_ladder(cfg(merge_edge=0.0), mid_yes=0.50, time_to_expiry=60)
    assert b == []


def test_balance_gate_suppresses_long_side():
    # Already long YES beyond the cap -> only NO bids posted (rebalance).
    b = compute_ladder(
        cfg(), mid_yes=0.50, time_to_expiry=60,
        inventory_yes_qty=30, inventory_no_qty=0,
    )
    assert best(b, "YES") is None
    assert best(b, "NO") is not None


def test_balance_gate_allows_within_cap():
    b = compute_ladder(
        cfg(max_naked_shares=20), mid_yes=0.50, time_to_expiry=60,
        inventory_yes_qty=10, inventory_no_qty=0,
    )
    assert best(b, "YES") is not None
    assert best(b, "NO") is not None


def test_per_market_cap_stops():
    b = compute_ladder(
        cfg(per_market_cap_usd=1.0), mid_yes=0.55, time_to_expiry=60,
        inventory_yes_qty=10, inventory_no_qty=10,
        inventory_yes_cost=5.0, inventory_no_cost=5.0,
    )
    assert b == []


def test_extreme_mid_skips_zero_price_leg():
    # mid 0.99 -> NO leg price = (1-0.99)-0.005 ~ 0.00 -> must not post <=0 bids.
    b = compute_ladder(cfg(), mid_yes=0.99, time_to_expiry=60)
    assert all(q.price > 0 for q in b)


def test_merge_edge_widens_spread():
    narrow = compute_ladder(cfg(merge_edge=0.01), mid_yes=0.55, time_to_expiry=60)
    wide = compute_ladder(cfg(merge_edge=0.04), mid_yes=0.55, time_to_expiry=60)
    assert best(wide, "YES") < best(narrow, "YES")


def test_time_to_expiry_gate():
    assert compute_ladder(cfg(), mid_yes=0.55, time_to_expiry=1) == []


def test_mid_out_of_bounds_empty():
    assert compute_ladder(cfg(), mid_yes=0.0, time_to_expiry=60) == []
    assert compute_ladder(cfg(), mid_yes=1.0, time_to_expiry=60) == []


def test_levels_count():
    b = compute_ladder(cfg(merge_levels=3), mid_yes=0.55, time_to_expiry=60)
    assert sum(1 for q in b if q.side == "YES") == 3
    assert sum(1 for q in b if q.side == "NO") == 3


def test_flat_size_on_every_bid():
    b = compute_ladder(cfg(flat_size=7), mid_yes=0.55, time_to_expiry=60)
    assert b and all(q.size == 7 for q in b)


def test_legacy_kwargs_ignored():
    # Caller may still pass leftover kwargs during transition; must not error.
    b = compute_ladder(
        cfg(), mid_yes=0.55, time_to_expiry=60,
        velocity_short=0.5, prev_mid_yes=0.5, committed_side="YES",
    )
    assert b
