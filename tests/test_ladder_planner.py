"""Pure laddered planner: rung placement, anchor toggle, clamps, caps, cost-basis gate."""

from quoter.config import Config
from quoter.runner.ladder_planner import LadderPlan, plan_ladder
from quoter.runner.requote_planner import RestingOrder


def cfg(**kw):
    base = dict(merge_edge=0.02, ladder_anchor="entry", rungs=5, rung_size=5,
                rung_spacing=0.03, naked_cap=50, per_window_cap=12.0)
    base.update(kw)
    return Config(**base)


def _plan(**kw):
    base = dict(yes_bid=0.44, no_bid=0.54, yes_ask=0.99, no_ask=0.99, entry_mid=0.45,
                inv_yes=0, inv_no=0, yes_cost=0.0, no_cost=0.0, committed=0.0,
                resting={"YES": [], "NO": []}, c=None, suppressed=frozenset())
    base.update(kw)
    c = base.pop("c") or cfg()
    return plan_ladder(
        yes_bid=base["yes_bid"], no_bid=base["no_bid"], yes_ask=base["yes_ask"],
        no_ask=base["no_ask"], entry_mid=base["entry_mid"], inv_yes=base["inv_yes"],
        inv_no=base["inv_no"], yes_cost=base["yes_cost"], no_cost=base["no_cost"],
        committed=base["committed"], resting=base["resting"], cfg=c,
        suppressed=base["suppressed"])


def _prices(posts, side):
    return sorted((q.price for q in posts if q.side == side), reverse=True)


def test_lays_ladder_from_entry_anchor():
    p = _plan()
    assert _prices(p.posts, "YES") == [0.44, 0.41, 0.38, 0.35, 0.32]
    assert _prices(p.posts, "NO") == [0.54, 0.51, 0.48, 0.45, 0.42]


def test_book_anchor_chases_current_bid():
    p = _plan(yes_bid=0.50, no_bid=0.48, c=cfg(ladder_anchor="book"))
    assert _prices(p.posts, "YES")[0] == 0.49
    assert _prices(p.posts, "NO")[0] == 0.47


def test_ask_clamp_drops_crossing_rungs():
    p = _plan(yes_ask=0.30)
    assert _prices(p.posts, "YES")[0] == 0.29


def test_pair_ok_blocks_expensive_completion_of_held_leg():
    p = _plan(inv_no=5, no_cost=5 * 0.62, committed=5 * 0.62)
    yp = _prices(p.posts, "YES")
    assert all(price <= 0.37 for price in yp)
    assert 0.35 in yp and 0.32 in yp


def test_naked_cap_pulls_heavier_side():
    resting = {"YES": [RestingOrder("y1", "YES", 0.44, 5)], "NO": []}
    p = _plan(inv_yes=10, inv_no=0, yes_cost=10 * 0.30, committed=3.0, resting=resting,
              c=cfg(naked_cap=10))
    assert "y1" in p.cancels
    assert not any(q.side == "YES" for q in p.posts)
    assert any(q.side == "NO" for q in p.posts)


def test_naked_cap_accounts_for_rung_size_no_overshoot():
    # the live bug: naked already 5 with cap 8 — a 5-share rung would push to 10 > 8,
    # so YES must NOT be posted (old `naked < cap` wrongly allowed it). NO has room.
    c = cfg(naked_cap=8, max_inflight_rungs=3)
    p = _plan(inv_yes=5, inv_no=0, yes_cost=5 * 0.44, committed=5 * 0.44, c=c)
    assert not any(q.side == "YES" for q in p.posts)   # 5 + 5 = 10 > cap 8 → blocked
    assert any(q.side == "NO" for q in p.posts)         # room (8+5)//5 = 2 rungs


def test_capital_cap_pulls_everything():
    resting = {"YES": [RestingOrder("y1", "YES", 0.44, 5)],
               "NO": [RestingOrder("n1", "NO", 0.54, 5)]}
    p = _plan(committed=12.0, resting=resting)
    assert set(p.cancels) == {"y1", "n1"} and p.posts == []


def test_keeps_existing_rung_reposts_missing():
    resting = {"YES": [RestingOrder("y1", "YES", 0.44, 5)], "NO": []}
    p = _plan(resting=resting)
    assert "y1" not in p.cancels
    assert _prices(p.posts, "YES") == [0.41, 0.38, 0.35, 0.32]


def test_trend_bias_suppresses_losing_side():
    # bias "UP" → Down is loser → no NO rungs posted; YES rungs still posted
    p_up = plan_ladder(
        yes_bid=0.44, no_bid=0.54, yes_ask=0.99, no_ask=0.99, entry_mid=0.45,
        inv_yes=0, inv_no=0, yes_cost=0.0, no_cost=0.0, committed=0.0,
        resting={"YES": [], "NO": []}, cfg=cfg(), trend_bias="UP")
    assert any(q.side == "YES" for q in p_up.posts)
    assert not any(q.side == "NO" for q in p_up.posts)
    # bias "DOWN" → Up is loser → no YES rungs
    p_dn = plan_ladder(
        yes_bid=0.44, no_bid=0.54, yes_ask=0.99, no_ask=0.99, entry_mid=0.45,
        inv_yes=0, inv_no=0, yes_cost=0.0, no_cost=0.0, committed=0.0,
        resting={"YES": [], "NO": []}, cfg=cfg(), trend_bias="DOWN")
    assert not any(q.side == "YES" for q in p_dn.posts)
    assert any(q.side == "NO" for q in p_dn.posts)


def test_trend_bias_neutral_is_default():
    # default NEUTRAL → both sides (same as no bias passed)
    p = plan_ladder(
        yes_bid=0.44, no_bid=0.54, yes_ask=0.99, no_ask=0.99, entry_mid=0.45,
        inv_yes=0, inv_no=0, yes_cost=0.0, no_cost=0.0, committed=0.0,
        resting={"YES": [], "NO": []}, cfg=cfg())
    assert any(q.side == "YES" for q in p.posts) and any(q.side == "NO" for q in p.posts)


def test_staged_posting_limits_to_inflight_rungs():
    # max_inflight_rungs=1 → only the top (shallowest) rung per side is desired
    c = cfg(max_inflight_rungs=1)
    p = _plan(c=c)
    assert _prices(p.posts, "YES") == [0.44]   # top YES rung only
    assert _prices(p.posts, "NO") == [0.54]     # top NO rung only


def test_staged_posting_advances_with_filled_depth():
    # 5 YES + 5 NO already filled (depth 1) → next rung of each side desired
    c = cfg(max_inflight_rungs=1)
    p = _plan(inv_yes=5, inv_no=5, yes_cost=5 * 0.44, no_cost=5 * 0.54,
              committed=5 * 0.98, c=c)
    assert _prices(p.posts, "YES") == [0.41]
    assert _prices(p.posts, "NO") == [0.51]


def test_suppressed_side_posts_nothing():
    # a suppressed side yields no posts and its existing rungs are cancelled;
    # the other side trades normally.
    resting = {"YES": [RestingOrder("y1", "YES", 0.44, 5)], "NO": []}
    p = _plan(resting=resting, c=cfg(naked_cap=50), suppressed=frozenset({"YES"}))
    assert not any(q.side == "YES" for q in p.posts)
    assert "y1" in p.cancels                       # existing YES rung pulled
    assert any(q.side == "NO" for q in p.posts)    # NO unaffected
