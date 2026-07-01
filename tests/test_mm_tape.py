from quoter.research.mm_tape import (
    normalize_trades,
    ticks_from_tape,
    decode_maker_buy,
    aggregate_fills,
)


def test_normalize_maps_and_sorts():
    raw = [
        {"timestamp": 20, "side": "SELL", "outcomeIndex": 1, "price": "0.4", "size": "3"},
        {"timestamp": 10, "side": "BUY", "outcomeIndex": 0, "price": "0.6", "size": "2"},
    ]
    out = normalize_trades(raw)
    assert [t["ts"] for t in out] == [10, 20]
    assert out[0] == {"ts": 10, "side": "BUY", "oi": 0, "price": 0.6, "size": 2.0}


def test_normalize_skips_incomplete():
    raw = [{"timestamp": 1, "side": "SELL"}, {"timestamp": 2, "side": "SELL",
            "outcomeIndex": 0, "price": "0.5", "size": "1"}]
    out = normalize_trades(raw)
    assert len(out) == 1 and out[0]["ts"] == 2


def test_ticks_sample_and_carry_mid():
    tape = [{"ts": 100, "side": "SELL", "oi": 0, "price": 0.55, "size": 1},
            {"ts": 160, "side": "SELL", "oi": 0, "price": 0.70, "size": 1}]
    ticks = ticks_from_tape(tape, open_ts=100, step_sec=60)
    # tick0 @100 mid=0.55; tick1 @160 mid=0.70
    assert ticks[0] == (100, 0.55)
    assert ticks[1] == (160, 0.70)


def test_ticks_fallback_mid_before_first_up_trade():
    tape = [{"ts": 130, "side": "SELL", "oi": 0, "price": 0.62, "size": 1}]
    ticks = ticks_from_tape(tape, open_ts=100, step_sec=60)
    assert ticks[0] == (100, 0.5)      # no Up trade yet at t=100


def test_decode_maker_buy_price_and_size():
    ev = {"makerAssetId": "0", "takerAssetId": "999", "makerAmountFilled": "50000", "takerAmountFilled": "5000000"}
    d = decode_maker_buy(ev)
    assert d["tid"] == "999" and abs(d["size"] - 5.0) < 1e-9 and abs(d["price"] - 0.01) < 1e-9


def test_decode_maker_buy_ignores_sell():
    assert decode_maker_buy({"makerAssetId": "123", "takerAssetId": "0", "makerAmountFilled": "5000000", "takerAmountFilled": "50000"}) is None


def test_aggregate_fills_cost_weighted():
    decoded = [
        {"slug": "s", "side": "Up", "price": 0.20, "size": 10},
        {"slug": "s", "side": "Up", "price": 0.40, "size": 10},
        {"slug": "s", "side": "Down", "price": 0.60, "size": 5},
    ]
    out = aggregate_fills(decoded)["s"]
    assert out["size_up"] == 20 and abs(out["avg_up"] - 0.30) < 1e-9
    assert out["size_dn"] == 5 and abs(out["avg_dn"] - 0.60) < 1e-9
