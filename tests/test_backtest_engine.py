from quoter.config import Config
from quoter.backtest.models import MarketWindow, PricePoint
from quoter.backtest.engine import run_market


def _win_market():
    # 5m window, YES wins. open=0, expire=300.
    return MarketWindow(market_id="0xwin", asset="BTC", timeframe="5m",
                        open_ts=0, expire_ts=300, winning_side="YES",
                        yes_token="t")


def test_momentum_inert_in_backtest_without_velocity():
    cfg = Config()
    # Backtest passes velocity_short=None → momentum leg never fires. With mid 0.55
    # the underdog price is 0.45 (>= lottery_max_price 0.40 → no lottery either),
    # so the engine produces no fills. Documents that phase-18 cannot be backtested
    # offline (no Binance velocity history).
    series = [PricePoint(120, 0.55), PricePoint(180, 0.55)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty == 0 and res.no_qty == 0
    assert res.pnl == 0.0


def test_no_favorite_no_fills_no_pnl():
    cfg = Config()
    # Flat coin-flip at 0.50 → favorite below favorite_min_price → no quotes.
    series = [PricePoint(120, 0.50), PricePoint(180, 0.50)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty == 0
    assert res.total_cost == 0.0
    assert res.pnl == 0.0
