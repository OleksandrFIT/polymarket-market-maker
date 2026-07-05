from quoter.runner.top_book_planner import plan_top_book, diff_quotes, plan_merge, TBQuote, skew_ok


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
