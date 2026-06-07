"""Replay one market window through the real compute_ladder + fill model."""

from __future__ import annotations

from quoter.config import Config
from quoter.strategy.ladder import compute_ladder
from quoter.backtest.fillsim import simulate_interval_fills
from quoter.backtest.models import BacktestResult, MarketWindow, PricePoint


def run_market(
    cfg: Config, window: MarketWindow, series: list[PricePoint],
) -> BacktestResult:
    """Replay `series` for `window` under `cfg`; return PnL at resolution."""
    yes_qty = no_qty = 0.0
    total_cost = 0.0
    n_fills = 0
    prev_yes: float | None = None

    for i in range(len(series) - 1):
        now, nxt = series[i], series[i + 1]
        tte = window.expire_ts - now.t
        if tte <= 0:
            break
        desired = compute_ladder(
            cfg,
            mid_yes=now.yes_price,
            time_to_expiry=float(tte),
            prev_mid_yes=prev_yes,
            inventory_yes_qty=int(yes_qty),
            inventory_no_qty=int(no_qty),
            timeframe=window.timeframe,
            asset=window.asset,
            window_length_sec=float(window.window_length),
        )
        for side, price, size in simulate_interval_fills(
            desired, now.yes_price, nxt.yes_price,
        ):
            if side == "YES":
                yes_qty += size
            else:
                no_qty += size
            total_cost += price * size
            n_fills += 1
        prev_yes = now.yes_price

    win_shares = yes_qty if window.winning_side == "YES" else no_qty
    pnl = win_shares * 1.0 - total_cost
    return BacktestResult(
        market_id=window.market_id, pnl=pnl, yes_qty=yes_qty,
        no_qty=no_qty, total_cost=total_cost, n_fills=n_fills,
    )
