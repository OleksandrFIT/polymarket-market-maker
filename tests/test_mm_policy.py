from quoter.research.mm_policy import guru_like_quotes, our_quotes, deep_ladder_quotes


def test_deep_ladder_both_sides_deep_below_mid():
    qs = deep_ladder_quotes(mid=0.50, size=100, levels=3, step=0.10)
    ups = [q.price for q in qs if q.side == "Up"]
    assert ups == [0.40, 0.30, 0.20]          # deep below mid, step 0.10
    assert all(q.size == 100 for q in qs)
    assert len([q for q in qs if q.side == "Down"]) == 3


def test_deep_ladder_skips_nonpositive():
    qs = deep_ladder_quotes(mid=0.50, size=10, levels=10, step=0.10)
    assert all(q.price > 0 for q in qs)


def test_guru_like_quotes_both_sides_laddered():
    qs = guru_like_quotes(mid=0.50, size=5, levels=2)
    ups = [q for q in qs if q.side == "Up"]
    dns = [q for q in qs if q.side == "Down"]
    assert len(ups) == 2 and len(dns) == 2
    assert [q.price for q in ups] == [0.49, 0.48]      # 1c below mid, laddered
    assert [q.price for q in dns] == [0.49, 0.48]      # Down price = 1-0.50 = 0.50
    assert all(q.size == 5 for q in qs)


def test_guru_like_skips_nonpositive_prices():
    qs = guru_like_quotes(mid=0.005, size=5, levels=3)   # Up price 0.005 -> ladder goes <=0
    assert all(q.price > 0 for q in qs)


def test_our_quotes_spread_and_levels():
    qs = our_quotes(mid=0.50, size=5, levels=2, spread=0.02, inventory={"Up": 0, "Down": 0})
    ups = [q for q in qs if q.side == "Up"]
    assert [round(q.price, 3) for q in ups] == [0.48, 0.47]   # spread below, then 1c
    assert all(q.size == 5 for q in qs)


def test_our_quotes_inventory_skew_halves_heavy_side():
    inv = {"Up": 100, "Down": 0}     # Up heavy (> size*levels = 5*2 = 10)
    qs = our_quotes(mid=0.50, size=5, levels=2, spread=0.02, inventory=inv)
    up_sizes = [q.size for q in qs if q.side == "Up"]
    dn_sizes = [q.size for q in qs if q.side == "Down"]
    assert all(s == 2.5 for s in up_sizes)     # halved
    assert all(s == 5 for s in dn_sizes)       # unchanged
