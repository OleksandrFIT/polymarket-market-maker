"""Phase-21 re-quoting: planner unit tests (incl. the 3 fixed bugs) + sim invariants.

All offline — proves behaviour WITHOUT any live trading.
"""

from quoter.config import Config
from quoter.runner.requote_planner import RestingOrder, plan_requote
from quoter.runner.requote_sim import RequoteSim


def cfg(**kw):
    base = dict(merge_edge=0.01, max_naked_shares=5, merge_levels=1,
                flat_size=5, per_market_cap_usd=50.0, min_time_to_expiry_sec=5.0)
    base.update(kw)
    return Config(**base)


def _plan(*, yes_bid=0.49, no_bid=0.49, yes_ask=None, no_ask=None,
          inv_yes=0, inv_no=0, yes_cost=0.0, no_cost=0.0, committed=0.0,
          target_shares=5, resting=None, c=None):
    return plan_requote(
        yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
        inv_yes=inv_yes, inv_no=inv_no, yes_cost=yes_cost, no_cost=no_cost,
        committed=committed, target_shares=target_shares,
        resting=resting or {"YES": None, "NO": None}, cfg=c or cfg())


def _sides(quotes):
    return {q.side for q in quotes}


# ── BUG 1: cost-basis edge gate (the real −$0.40 window) ──

def test_bug1_does_not_complete_losing_pair():
    # hold 5 YES bought @ 0.46; NO now 0.62 → pair would be 1.08 → must NOT buy NO
    p = _plan(inv_yes=5, inv_no=0, yes_cost=5 * 0.46, committed=5 * 0.46,
              yes_bid=0.36, no_bid=0.62)
    assert all(q.side != "NO" for q in p.posts)  # refuses the losing completion


def test_bug1_completes_when_pair_stays_under_1():
    # hold 5 YES @ 0.46; NO now 0.50 → pair 0.96 < 1 → completing is fine
    p = _plan(inv_yes=5, inv_no=0, yes_cost=5 * 0.46, committed=5 * 0.46,
              yes_bid=0.46, no_bid=0.50)
    assert any(q.side == "NO" for q in p.posts)


def test_bug1_symmetric_for_no_held():
    # hold 5 NO @ 0.46; YES now 0.62 → pair 1.08 → must NOT buy YES
    p = _plan(inv_no=5, inv_yes=0, no_cost=5 * 0.46, committed=5 * 0.46,
              no_bid=0.36, yes_bid=0.62)
    assert all(q.side != "YES" for q in p.posts)


# ── BUG 2: per-side target (no over-buying) ──

def test_bug2_target_caps_a_side():
    p = _plan(inv_no=5, inv_yes=0, no_cost=5 * 0.30, committed=1.5,
              target_shares=5, yes_bid=0.49, no_bid=0.30)
    assert all(q.side != "NO" for q in p.posts)  # NO already at target


# ── BUG 3: post stays below the ask (never crosses) ──

def test_bug3_clamps_below_ask():
    p = _plan(yes_bid=0.50, yes_ask=0.50, no_bid=0.48, no_ask=0.50)  # YES bid==ask
    yp = [q for q in p.posts if q.side == "YES"]
    assert yp and yp[0].price <= 0.49


# ── standard planner behaviour ──

def test_posts_both_when_balanced_and_cheap():
    p = _plan()
    assert _sides(p.posts) == {"YES", "NO"}


def test_capital_gate_pulls_everything():
    resting = {"YES": RestingOrder("a", "YES", 0.49, 5), "NO": RestingOrder("b", "NO", 0.49, 5)}
    p = _plan(resting=resting, committed=50.0, c=cfg(per_market_cap_usd=50.0))
    assert set(p.cancels) == {"a", "b"} and p.posts == []


def test_keep_order_when_price_unchanged():
    resting = {"YES": RestingOrder("a", "YES", 0.49, 5), "NO": RestingOrder("b", "NO", 0.49, 5)}
    p = _plan(resting=resting, yes_bid=0.49, no_bid=0.49)
    assert p.cancels == [] and p.posts == []


def test_requote_on_price_move():
    resting = {"YES": RestingOrder("a", "YES", 0.48, 5), "NO": None}
    p = _plan(resting=resting, yes_bid=0.50, no_bid=0.49)
    assert "a" in p.cancels and any(q.side == "YES" and q.price == 0.50 for q in p.posts)


# ── simulation invariants (the heart of "max testing") ──

def test_sim_never_completes_a_losing_pair():
    # YES fills cheap, then NO is only available expensive (the −$0.40 trap).
    sim = RequoteSim(cfg=cfg(), target_shares=5)
    sim.run([(0.46, 0.49, "YES")] + [(0.36, 0.62, "NO")] * 4)
    assert sim.inv_no == 0          # refused to buy NO @ 0.62 (would lock a loss)
    assert sim.inv_yes == 5         # holds the naked leg instead — never a >$1 pair


def test_sim_matched_pairs_always_cheap():
    sim = RequoteSim(cfg=cfg(), target_shares=5)
    sim.run([(0.49, 0.49, "YES" if i % 2 == 0 else "NO") for i in range(12)])
    if sim.matched > 0:
        assert sim.avg_pair_cost() < 1.0


def test_sim_target_no_overbuy():
    sim = RequoteSim(cfg=cfg(), target_shares=5)
    sim.run([(0.49, 0.30, "NO")] * 12)          # cheap NO hammered repeatedly
    assert sim.inv_no <= 5                       # never exceeds per-side target


def test_sim_balanced_flow_catches_exactly_the_pair():
    sim = RequoteSim(cfg=cfg(), target_shares=5)
    sim.run([(0.49, 0.49, "YES" if i % 2 == 0 else "NO") for i in range(12)])
    assert sim.matched == 5 and sim.naked == 0
    assert sim.avg_pair_cost() < 1.0


def test_sim_naked_bounded_under_one_sided_flow():
    c = cfg(max_naked_shares=5)
    sim = RequoteSim(cfg=c, target_shares=5)
    sim.run([(0.49, 0.49, "YES")] * 20)
    assert sim.max_naked <= c.max_naked_shares  # target+naked cap hold (lands on 5)
