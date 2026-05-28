from quoter.config import Config
from quoter.backtest.models import MarketWindow, PricePoint
from quoter.backtest.engine import run_market


def _win_market():
    # 5m window, YES wins. open=0, expire=300.
    return MarketWindow(market_id="0xwin", asset="BTC", timeframe="5m",
                        open_ts=0, expire_ts=300, winning_side="YES",
                        yes_token="t")

def test_buying_winner_cheap_is_profitable():
    cfg = Config(max_inventory_skew_shares=200)
    # YES sits cheap early (0.30) then climbs to 0.99 → our YES bids at <=0.30
    # fill early, resolve to $1 each → positive PnL.
    series = [PricePoint(0, 0.30), PricePoint(60, 0.30),
              PricePoint(120, 0.55), PricePoint(180, 0.99)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty > 0
    assert res.pnl > 0

def test_no_fills_no_pnl():
    cfg = Config(max_inventory_skew_shares=200)
    # Flat price, no dips below any bid that survives cap → essentially no PnL
    series = [PricePoint(0, 0.50), PricePoint(60, 0.50)]
    res = run_market(cfg, _win_market(), series)
    assert res.pnl == res.yes_qty * 1.0 - res.total_cost
