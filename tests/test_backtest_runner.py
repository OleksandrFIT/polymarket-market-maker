from dataclasses import replace
from quoter.config import Config
from quoter.backtest.models import MarketWindow, PricePoint
from quoter.backtest.run_backtest import run_config


def _market():
    return MarketWindow(market_id="0xa", asset="BTC", timeframe="5m",
                        open_ts=0, expire_ts=300, winning_side="YES",
                        yes_token="t")

def test_run_config_aggregates_pnl():
    cfg = Config(max_inventory_skew_shares=200)
    series = {"0xa": [PricePoint(0, 0.30), PricePoint(60, 0.30),
                      PricePoint(120, 0.99)]}
    summary = run_config(cfg, [_market()], series)
    assert summary.n_markets == 1
    assert summary.total_pnl == summary.results[0].pnl
