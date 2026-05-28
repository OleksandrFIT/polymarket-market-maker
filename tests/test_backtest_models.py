from quoter.backtest.models import MarketWindow, PricePoint, BacktestResult


def test_market_window_fields():
    m = MarketWindow(market_id="0xabc", asset="BTC", timeframe="5m",
                     open_ts=100, expire_ts=400, winning_side="YES",
                     yes_token="tok")
    assert m.window_length == 300

def test_backtest_result_aggregate():
    r = BacktestResult(market_id="0xabc", pnl=12.5, yes_qty=10, no_qty=3,
                       total_cost=5.0, n_fills=4)
    assert r.pnl == 12.5
    assert r.n_fills == 4
