from quoter.config import Config
from quoter.backtest.models import MarketWindow, PricePoint
from quoter.backtest.engine import run_market


def _win_market():
    # 5m window, YES wins. open=0, expire=300.
    return MarketWindow(market_id="0xwin", asset="BTC", timeframe="5m",
                        open_ts=0, expire_ts=300, winning_side="YES",
                        yes_token="t")


def test_buying_winning_favorite_is_profitable():
    cfg = Config()
    # Late, rising YES favorite in the >=0.85 band during the last 40% of a 5m
    # window (open=0, expire=300): times 200,240,280,295 → window_frac 0.67..0.98.
    series = [PricePoint(200, 0.86), PricePoint(240, 0.90),
              PricePoint(280, 0.95), PricePoint(295, 0.99)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty > 0
    assert res.pnl > 0


def test_no_favorite_no_fills_no_pnl():
    cfg = Config()
    # Flat coin-flip at 0.50 → favorite below favorite_min_price → no quotes.
    series = [PricePoint(120, 0.50), PricePoint(180, 0.50)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty == 0
    assert res.total_cost == 0.0
    assert res.pnl == 0.0
