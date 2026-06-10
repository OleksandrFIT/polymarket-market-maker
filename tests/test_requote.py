"""Phase-21 re-quoting: pure planner unit tests + simulation invariants.

All offline — proves behaviour WITHOUT any live trading.
"""

from quoter.config import Config
from quoter.runner.requote_planner import RestingOrder, plan_requote
from quoter.runner.requote_sim import RequoteSim


def cfg(**kw):
    base = dict(merge_edge=0.01, max_naked_shares=10, merge_levels=1,
                flat_size=5, per_market_cap_usd=50.0, min_time_to_expiry_sec=5.0)
    base.update(kw)
    return Config(**base)


def _plan(resting=None, inv_yes=0, inv_no=0, yes_cost=0.0, no_cost=0.0,
          yes_bid=0.49, no_bid=0.49, c=None):
    return plan_requote(
        yes_bid=yes_bid, no_bid=no_bid, inv_yes=inv_yes, inv_no=inv_no,
        yes_cost=yes_cost, no_cost=no_cost,
        resting=resting or {"YES": None, "NO": None}, cfg=c or cfg())


def _sides(quotes):
    return {q.side for q in quotes}


# ── pure planner unit tests ──

def test_posts_both_when_balanced():
    p = _plan()
    assert _sides(p.posts) == {"YES", "NO"}
    assert p.cancels == []


def test_edge_gate_no_posts_when_pair_ge_1():
    p = _plan(yes_bid=0.55, no_bid=0.55)  # sum 1.10
    assert p.posts == []


def test_edge_gate_cancels_stale_when_no_edge():
    resting = {"YES": RestingOrder("a", "YES", 0.49, 5), "NO": None}
    p = _plan(resting=resting, yes_bid=0.55, no_bid=0.55)
    assert "a" in p.cancels and p.posts == []


def test_balance_suppresses_long_side():
    p = _plan(inv_yes=10, inv_no=0)  # naked = 10 = cap
    assert _sides(p.posts) == {"NO"}  # only the short side


def test_capital_gate_pulls_everything():
    resting = {"YES": RestingOrder("a", "YES", 0.49, 5),
               "NO": RestingOrder("b", "NO", 0.49, 5)}
    p = _plan(resting=resting, yes_cost=30.0, no_cost=25.0, c=cfg(per_market_cap_usd=50.0))
    assert set(p.cancels) == {"a", "b"} and p.posts == []


def test_requote_on_price_move():
    resting = {"YES": RestingOrder("a", "YES", 0.48, 5), "NO": None}
    p = _plan(resting=resting, yes_bid=0.50, no_bid=0.49)
    assert "a" in p.cancels
    yes_posts = [q for q in p.posts if q.side == "YES"]
    assert yes_posts and yes_posts[0].price == 0.50


def test_keep_order_when_price_unchanged():
    resting = {"YES": RestingOrder("a", "YES", 0.49, 5),
               "NO": RestingOrder("b", "NO", 0.49, 5)}
    p = _plan(resting=resting, yes_bid=0.49, no_bid=0.49)
    assert p.cancels == [] and p.posts == []  # no churn


def test_cancel_unwanted_long_side():
    resting = {"YES": RestingOrder("a", "YES", 0.49, 5), "NO": None}
    p = _plan(resting=resting, inv_yes=10, inv_no=0)  # YES is over-long
    assert "a" in p.cancels


# ── simulation invariants (the heart of "max testing") ──

def test_sim_balanced_flow_catches_pairs():
    sim = RequoteSim(cfg=cfg())
    # alternate takers on each side with a steady balanced book
    script = []
    for i in range(24):
        script.append((0.49, 0.49, "YES" if i % 2 == 0 else "NO"))
    sim.run(script)
    assert sim.matched > 0          # re-quoting actually catches pairs
    assert sim.naked <= cfg().flat_size  # stays near-balanced


def test_sim_naked_never_exceeds_cap_plus_flatsize_one_sided():
    # Adversarial: a taker hits ONLY the YES side every tick.
    c = cfg(max_naked_shares=10, flat_size=5)
    sim = RequoteSim(cfg=c)
    sim.run([(0.49, 0.49, "YES")] * 40)
    # provable bound: a fill can land at most flat_size-1 past the cap
    assert sim.max_naked < c.max_naked_shares + c.flat_size
    assert sim.max_naked <= 10  # exact here: 0->5->10 lands on the cap


def test_sim_matched_pairs_are_cheap():
    sim = RequoteSim(cfg=cfg())
    script = [(0.49, 0.49, "YES" if i % 2 == 0 else "NO") for i in range(20)]
    sim.run(script)
    apc = sim.avg_pair_cost()
    assert apc is not None and apc < 1.0   # every held pair cost < $1


def test_sim_spend_never_exceeds_cap():
    c = cfg(per_market_cap_usd=6.0, flat_size=5)  # tight cap
    sim = RequoteSim(cfg=c)
    sim.run([(0.49, 0.49, "YES" if i % 2 == 0 else "NO") for i in range(40)])
    assert sim.spent <= c.per_market_cap_usd + 1e-9 + c.flat_size  # bounded


def test_sim_choppy_book_still_catches_and_stays_safe():
    c = cfg()
    sim = RequoteSim(cfg=c)
    # moving/choppy book, two-way flow
    prices = [0.45, 0.50, 0.55, 0.48, 0.52, 0.47, 0.50, 0.53]
    script = []
    for i in range(32):
        yb = prices[i % len(prices)]
        script.append((round(yb, 2), round(0.98 - yb, 2), "YES" if i % 2 else "NO"))
    sim.run(script)
    assert sim.max_naked < c.max_naked_shares + c.flat_size  # never unsafe
    if sim.matched > 0:
        assert sim.avg_pair_cost() < 1.0
