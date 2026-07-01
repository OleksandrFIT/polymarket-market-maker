from quoter.research.mm_book import depth_ahead, queue_fill


def test_depth_ahead_sums_levels_at_or_above_price():
    bids = [[0.60, 10], [0.50, 20], [0.40, 5]]
    assert depth_ahead(bids, 0.50) == 30.0     # 0.60 and 0.50
    assert depth_ahead(bids, 0.55) == 10.0     # only 0.60
    assert depth_ahead(bids, 0.40) == 35.0
    assert depth_ahead([], 0.50) == 0.0


def S(ts, side, price, size):
    return {"ts": ts, "side": side, "price": price, "size": size}


def test_queue_not_breached_no_fill():
    # ahead=30, only 20 sold through our price -> 0
    tape = [S(5, "SELL", 0.49, 20)]
    assert queue_fill(0.50, 100, 0, [[0.50, 30]], tape) == 0.0


def test_queue_partially_breached_partial_fill():
    # ahead=30, 50 sold -> 20 reaches us, size 100 -> 20
    tape = [S(5, "SELL", 0.49, 50)]
    assert queue_fill(0.50, 100, 0, [[0.50, 30]], tape) == 20.0


def test_our_size_caps_fill():
    tape = [S(5, "SELL", 0.49, 100)]
    assert queue_fill(0.50, 5, 0, [], tape) == 5.0     # ahead 0, plenty sold, capped at 5


def test_taker_buy_ignored():
    tape = [S(5, "BUY", 0.49, 100)]
    assert queue_fill(0.50, 10, 0, [], tape) == 0.0


def test_trade_above_our_price_ignored():
    tape = [S(5, "SELL", 0.60, 100)]      # 0.60 > our 0.50 bid -> doesn't reach us
    assert queue_fill(0.50, 10, 0, [], tape) == 0.0


def test_trades_before_placement_ignored():
    tape = [S(1, "SELL", 0.49, 100)]      # before placed_ts=5
    assert queue_fill(0.50, 10, 5, [], tape) == 0.0
