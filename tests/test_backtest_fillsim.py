from quoter.strategy.ladder import Quote
from quoter.backtest.fillsim import simulate_interval_fills


def test_yes_bid_fills_when_price_dips_to_it():
    # YES bid at 0.40; YES price moves 0.50 -> 0.38 (dips below 0.40) → fills
    q = Quote("YES", 0.40, 10)
    fills = simulate_interval_fills([q], yes_now=0.50, yes_next=0.38)
    assert fills == [("YES", 0.40, 10)]

def test_yes_bid_no_fill_when_price_stays_above():
    q = Quote("YES", 0.40, 10)
    fills = simulate_interval_fills([q], yes_now=0.50, yes_next=0.45)
    assert fills == []

def test_no_bid_fills_when_no_price_dips():
    # NO bid at 0.40; NO price = 1 - yes. yes 0.50 -> 0.65 => no 0.50 -> 0.35
    # (dips below 0.40) → fills
    q = Quote("NO", 0.40, 7)
    fills = simulate_interval_fills([q], yes_now=0.50, yes_next=0.65)
    assert fills == [("NO", 0.40, 7)]

def test_no_bid_no_fill():
    q = Quote("NO", 0.40, 7)
    fills = simulate_interval_fills([q], yes_now=0.50, yes_next=0.55)
    assert fills == []
