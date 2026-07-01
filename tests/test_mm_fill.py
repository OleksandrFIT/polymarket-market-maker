from quoter.research.mm_fill import fill
from quoter.research.mm_types import Theta


def T(ts, side, oi, price, size):
    return {"ts": ts, "side": side, "oi": oi, "price": price, "size": size}


def test_bid_fills_from_crossing_taker_sell():
    # bid Up @0.50, taker SELLs Up 10 @0.48 (<=0.50) -> we capture fill_frac of 10
    r = fill("Up", 0.50, 100, [T(1, "SELL", 0, 0.48, 10)], Theta(fill=1.0))
    assert r.filled == 10.0
    assert r.avg_price == 0.50          # maker fills at OUR price


def test_no_fill_when_trade_price_above_bid():
    r = fill("Up", 0.50, 100, [T(1, "SELL", 0, 0.55, 10)], Theta(fill=1.0))
    assert r.filled == 0.0


def test_wrong_side_no_fill():
    # a SELL of Down does not fill our Up bid
    r = fill("Up", 0.50, 100, [T(1, "SELL", 1, 0.40, 10)], Theta(fill=1.0))
    assert r.filled == 0.0


def test_taker_buy_does_not_fill_our_bid():
    # our BID needs a taker SELL; a taker BUY hits asks, not our bid
    r = fill("Up", 0.50, 100, [T(1, "BUY", 0, 0.48, 10)], Theta(fill=1.0))
    assert r.filled == 0.0


def test_queue_haircut_fill_frac():
    r = fill("Up", 0.50, 100, [T(1, "SELL", 0, 0.48, 10)], Theta(fill=0.3))
    assert abs(r.filled - 3.0) < 1e-9


def test_capped_at_our_size():
    r = fill("Up", 0.50, 5, [T(1, "SELL", 0, 0.48, 100)], Theta(fill=1.0))
    assert r.filled == 5.0


def test_accumulates_across_multiple_crossings():
    tape = [T(1, "SELL", 0, 0.49, 4), T(2, "SELL", 0, 0.47, 4)]
    r = fill("Up", 0.50, 100, tape, Theta(fill=1.0))
    assert r.filled == 8.0
