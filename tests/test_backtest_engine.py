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
    # Late, rising YES favorite (0.70 → 0.96) that resolves YES. Our top bid
    # sits at the favorite price each interval, so YES fills cheap-of-1.0 and
    # the winning shares pay out 1.0 → positive PnL.
    series = [PricePoint(120, 0.70), PricePoint(180, 0.80),
              PricePoint(240, 0.90), PricePoint(290, 0.96)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty > 0
    assert res.pnl > 0


def test_no_favorite_no_fills_no_pnl():
    cfg = Config()
    # Flat coin-flip at 0.50 → favorite below favorite_min_price → no quotes.
    series = [PricePoint(120, 0.50), PricePoint(180, 0.50)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty == 0
    assert res.pnl == res.yes_qty * 1.0 - res.total_cost
