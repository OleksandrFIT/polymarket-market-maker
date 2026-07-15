from quoter.runner.top_book_planner import (
    plan_top_book, diff_quotes, plan_merge, TBQuote, skew_ok, link_pair_bids)


def test_link_pair_caps_light_bid_below_one():
    # hold 5 Up @ avg 0.32; Down (light) quote 0.741 (market moved) -> capped to 1-0.32-0.01=0.67
    tgt = [TBQuote("Down", 0.741, 5.0)]
    out = link_pair_bids(tgt, {"Up": 5.0, "Down": 0.0}, {"Up": 0.32, "Down": None}, 0.01)
    assert out[0].side == "Down" and out[0].price == 0.67   # pair 0.32+0.67 = 0.99 < $1


def test_link_pair_keeps_bid_when_already_below_cap():
    # Down quote 0.65 is already < cap 0.67 -> keep it (don't raise)
    out = link_pair_bids([TBQuote("Down", 0.65, 5.0)], {"Up": 5.0, "Down": 0.0},
                         {"Up": 0.32, "Down": None}, 0.01)
    assert out[0].price == 0.65


def test_link_pair_leaves_heavy_side_untouched():
    # quoting the HEAVY side (Up, which we hold more of) is not capped
    out = link_pair_bids([TBQuote("Up", 0.481, 5.0)], {"Up": 5.0, "Down": 0.0},
                         {"Up": 0.32, "Down": None}, 0.01)
    assert out[0].price == 0.481


def test_link_pair_balanced_low_sum_unchanged():
    # no holdings AND the two bids sum < 1 - margin -> unchanged (nothing to pair against, sum safe)
    tgt = [TBQuote("Up", 0.481, 5.0), TBQuote("Down", 0.491, 5.0)]   # sum 0.972 < 0.99
    out = link_pair_bids(tgt, {"Up": 0.0, "Down": 0.0}, {"Up": None, "Down": None}, 0.01)
    assert [(q.side, q.price) for q in out] == [("Up", 0.481), ("Down", 0.491)]


def test_link_pair_joint_sum_caps_from_empty():
    # from an empty book both avg None, bids sum 1.002 >= $1 -> WITHOUT the joint-sum cap two
    # simultaneous fills would assemble a pair >= $1 (the case-audit leak). The cap reduces both so
    # the sum = 1 - margin = 0.99.
    tgt = [TBQuote("Up", 0.481, 5.0), TBQuote("Down", 0.521, 5.0)]   # sum 1.002
    out = link_pair_bids(tgt, {"Up": 0.0, "Down": 0.0}, {"Up": None, "Down": None}, 0.01)
    prices = {q.side: q.price for q in out}
    assert prices["Up"] + prices["Down"] <= 0.99 + 1e-9
    assert abs(prices["Up"] - 0.475) < 1e-9 and abs(prices["Down"] - 0.515) < 1e-9


def test_link_pair_two_sided_caps_the_heavier_side():
    # TWO-SIDED cap (the invariant fix): holding BOTH legs, quoting Up while Down avg is 0.55 ->
    # Up is capped against Down's avg (1 - 0.55 - 0.01 = 0.44) even though Up is NOT the lighter
    # side. The OLD one-sided rule left Up uncapped -> 0.50 + 0.55 = 1.05 pair (the leak).
    out = link_pair_bids([TBQuote("Up", 0.50, 5.0)], {"Up": 5.0, "Down": 5.0},
                         {"Up": 0.50, "Down": 0.55}, 0.01)
    assert abs(out[0].price - 0.44) < 1e-9


def test_link_pair_drops_side_when_cap_nonpositive():
    # heavy avg 0.995 -> cap = 1-0.995-0.01 = -0.005 -> can't pair profitably -> drop
    out = link_pair_bids([TBQuote("Down", 0.02, 5.0)], {"Up": 5.0, "Down": 0.0},
                         {"Up": 0.995, "Down": None}, 0.01)
    assert out == []


def test_link_pair_off_when_margin_zero():
    tgt = [TBQuote("Down", 0.741, 5.0)]
    out = link_pair_bids(tgt, {"Up": 5.0, "Down": 0.0}, {"Up": 0.32, "Down": None}, 0.0)
    assert out[0].price == 0.741                            # feature off -> unchanged


def test_skew_ok_boundary():
    # cap 10, size 5: reachable to EXACTLY cap, not past it
    assert skew_ok(5, 0, 5, 10) is True     # 5+5-0 = 10 <= 10  -> fills to exactly cap
    assert skew_ok(6, 0, 5, 10) is False    # 6+5-0 = 11 > 10   -> would overshoot
    assert skew_ok(0, 0, 5, 10) is True     # balanced start
    assert skew_ok(10, 0, 5, 10) is False   # already at cap -> no more


def test_skew_ok_light_side_never_blocked():
    # under-weight side rebalances freely even when heavily skewed
    assert skew_ok(0, 15, 5, 10) is True    # 0+5-15 = -10 <= 10


def B(levels):  # CLOB-style book side
    return [{"price": str(p), "size": str(s)} for p, s in levels]


YES = {"bids": B([(0.01, 100), (0.48, 50)]), "asks": B([(0.99, 100), (0.52, 40)])}
NO = {"bids": B([(0.01, 100), (0.46, 30)]), "asks": B([(0.99, 100), (0.54, 20)])}


def test_quotes_best_plus_tick_both_sides():
    qs = plan_top_book(YES, NO, inv_up=0, inv_dn=0, naked_cap=10, size=5, tick=0.001)
    d = {q.side: q for q in qs}
    assert d["Up"].price == 0.481          # max bid 0.48 + tick
    assert d["Down"].price == 0.461
    assert d["Up"].size == 5 and d["Down"].size == 5


def test_never_cross_the_spread():
    tight = {"bids": B([(0.52, 10)]), "asks": B([(0.521, 10)])}   # bid+tick == ask
    qs = plan_top_book(tight, NO, 0, 0, 10, 5, 0.001)
    assert all(q.side != "Up" for q in qs)


def test_gate_099():
    hi = {"bids": B([(0.99, 10)]), "asks": B([])}
    qs = plan_top_book(hi, NO, 0, 0, 10, 5, 0.001)
    assert all(q.side != "Up" for q in qs)


def test_skew_cap_stops_heavy_side():
    qs = plan_top_book(YES, NO, inv_up=15, inv_dn=0, naked_cap=10, size=5, tick=0.001)
    assert all(q.side != "Up" for q in qs)
    assert any(q.side == "Down" for q in qs)


def test_hard_skew_stops_before_overshoot():
    # Hard cap: the size-`size` order we'd rest here can itself fill. At filled-naked 6,
    # a full size-5 fill pushes naked to 11 (> cap 10). The OLD gate (inv-inv >= cap)
    # allowed this (6 < 10) -> the burst-fill overshoot seen live. The hard gate counts
    # the in-flight order's full size and must stop the heavy side here.
    qs = plan_top_book(YES, NO, inv_up=6, inv_dn=0, naked_cap=10, size=5, tick=0.001)
    assert all(q.side != "Up" for q in qs)
    assert any(q.side == "Down" for q in qs)


def test_hard_skew_allows_reaching_exactly_cap():
    # At filled-naked 5 the resting size-5 order fills to EXACTLY cap (10), never past it,
    # so quoting stays enabled — the cap is reachable, just not breachable.
    qs = plan_top_book(YES, NO, inv_up=5, inv_dn=0, naked_cap=10, size=5, tick=0.001)
    assert any(q.side == "Up" for q in qs)


def test_empty_bids_skips_side():
    qs = plan_top_book({"bids": [], "asks": B([(0.6, 5)])}, NO, 0, 0, 10, 5, 0.001)
    assert all(q.side != "Up" for q in qs)


def test_pair_cost_gate_keeps_only_cheaper_side():
    yes = {"bids": B([(0.52, 10)]), "asks": B([(0.60, 10)])}
    no = {"bids": B([(0.48, 10)]), "asks": B([(0.56, 10)])}
    # ours would be 0.521 + 0.481 = 1.002 > 0.999 -> keep only Down (cheaper)
    qs = plan_top_book(yes, no, 0, 0, 10, 5, 0.001)
    assert [(q.side, q.price) for q in qs] == [("Down", 0.481)]


def test_diff_quotes_keep_cancel_post():
    cur = {"Up": (0.481, 5.0), "Down": (0.40, 5.0)}      # Down price now stale
    tgt = [TBQuote("Up", 0.481, 5.0), TBQuote("Down", 0.461, 5.0)]
    cancel, post = diff_quotes(cur, tgt)
    assert cancel == ["Down"]
    assert [(q.side, q.price) for q in post] == [("Down", 0.461)]


def test_diff_quotes_cancels_side_missing_from_target():
    cur = {"Up": (0.481, 5.0)}
    cancel, post = diff_quotes(cur, [])                   # Up gated out now
    assert cancel == ["Up"] and post == []


def test_diff_quotes_ignores_size_partial_fill_keeps_priority():
    cur = {"Up": (0.481, 3.0)}                    # partially filled (was 5)
    tgt = [TBQuote("Up", 0.481, 5.0)]
    cancel, post = diff_quotes(cur, tgt)
    assert cancel == [] and post == []            # keep: same price, queue priority preserved


def test_plan_merge():
    assert plan_merge(12.0, 7.0, merge_min=5.0) == 7.0
    assert plan_merge(3.0, 7.0, merge_min=5.0) == 0.0     # min(3,7)=3 < 5
    assert plan_merge(0.0, 7.0, merge_min=5.0) == 0.0
