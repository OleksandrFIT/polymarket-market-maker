"""Run one or more configs over cached market windows; print an A/B table.

CLI:  .venv/bin/python -m quoter.backtest.run_backtest
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

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


def sweep() -> None:
    """Grid over entry_cutoff_frac x max_entry_price; print PnL per combo."""
    markets = load_markets_from_db()
    series_by_market = {m.market_id: fetch_price_series(m) for m in markets}
    summaries: list[ConfigSummary] = []
    for cutoff in (0.40, 0.50, 0.60, 0.75):
        for cap in (0.50, 0.55, 0.60, 0.70):
            cfg = replace(Config(), entry_cutoff_frac=cutoff, max_entry_price=cap)
            summaries.append(
                run_config(cfg, markets, series_by_market,
                           f"cut={cutoff} cap={cap}")
            )
    summaries.sort(key=lambda s: s.total_pnl, reverse=True)
    _print_table(summaries)


def main() -> None:
    markets = load_markets_from_db()
    series_by_market = {m.market_id: fetch_price_series(m) for m in markets}

    baseline = replace(
        Config(), directional_filter_enabled=True,
        directional_size_skew_enabled=True, entry_cutoff_frac=1.0,
        max_entry_price=0.99,
    )
    new_tactic = Config()  # phase-14 defaults

    summaries = [
        run_config(baseline, markets, series_by_market, "baseline(phase-13)"),
        run_config(new_tactic, markets, series_by_market, "new(early+cap)"),
    ]
    _print_table(summaries)


if __name__ == "__main__":
    import sys
    sweep() if "--sweep" in sys.argv else main()
