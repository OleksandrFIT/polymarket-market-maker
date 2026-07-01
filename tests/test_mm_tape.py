from quoter.research.mm_tape import normalize_trades, ticks_from_tape


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
