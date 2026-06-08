"""Run one or more configs over cached market windows; print an A/B table.

CLI:  .venv/bin/python -m quoter.backtest.run_backtest
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.backtest.engine import run_market
from quoter.backtest.fetch import fetch_price_series, load_markets_from_db
from quoter.backtest.models import BacktestResult, MarketWindow, PricePoint


@dataclass
class ConfigSummary:
    name: str
    n_markets: int
    total_pnl: float
    wins: int
    worst: float
    results: list[BacktestResult] = field(default_factory=list)


def run_config(
    cfg: Config, markets: list[MarketWindow],
    series_by_market: dict[str, list[PricePoint]], name: str = "cfg",
) -> ConfigSummary:
    results: list[BacktestResult] = []
    for m in markets:
        series = series_by_market.get(m.market_id, [])
        if len(series) < 2:
            continue
        results.append(run_market(cfg, m, series))
    total = sum(r.pnl for r in results)
    wins = sum(1 for r in results if r.pnl > 0)
    worst = min((r.pnl for r in results), default=0.0)
    return ConfigSummary(name, len(results), total, wins, worst, results)


def _print_table(summaries: list[ConfigSummary]) -> None:
    print(f"\n{'config':28} {'n':>4} {'total PnL':>12} {'win-rate':>9} {'worst':>10}")
    print("-" * 66)
    for s in summaries:
        wr = f"{s.wins}/{s.n_markets}"
        print(f"{s.name:28} {s.n_markets:>4} {s.total_pnl:>12.2f} {wr:>9} {s.worst:>10.2f}")


def main() -> None:
    """Run the phase-19 merge-maker over cached windows and print a table.

    NOTE: the offline fill model fills only the falling leg (no live queue
    priority), so backtest PnL is descriptive, NOT a profit claim — the
    merge-maker edge is realizable only live in us-east-1. See the phase-19
    spec for the honest framing.
    """
    markets = load_markets_from_db()
    series_by_market = {m.market_id: fetch_price_series(m) for m in markets}
    summary = run_config(Config(), markets, series_by_market, "phase-19(merge-maker)")
    _print_table([summary])


if __name__ == "__main__":
    main()
