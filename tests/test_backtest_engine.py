from quoter.config import Config
from quoter.backtest.models import MarketWindow, PricePoint
from quoter.backtest.engine import run_market


def _win_market():
    # 5m window, YES wins. open=0, expire=300.
    return MarketWindow(market_id="0xwin", asset="BTC", timeframe="5m",
                        open_ts=0, expire_ts=300, winning_side="YES",
                        yes_token="t")


def test_no_quotes_below_min_tte():
    cfg = Config()  # min_time_to_expiry_sec = 5.0
    # Last ticks of the window (tte < 5s) -> compute_ladder returns no bids,
    # so the engine produces no fills regardless of price.
    series = [PricePoint(297, 0.55), PricePoint(299, 0.50)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty == 0 and res.no_qty == 0
    assert res.total_cost == 0.0
    assert res.pnl == 0.0


def test_falling_leg_fills_offline():
    cfg = Config()
    # YES price dips 0.55 -> 0.50, crossing our YES bid (~0.545). The offline
    # model fills the FALLING leg only (the NO bid at ~0.445 is not reached),
    # which is exactly the back-of-queue adverse-selection the merge-maker faces
    # in paper — naked exposure, bounded live by us-east-1 queue priority.
    series = [PricePoint(120, 0.55), PricePoint(180, 0.50)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty > 0
    assert res.no_qty == 0
