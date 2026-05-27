"""Tests for QuoterLoop per-market risk gating."""

from __future__ import annotations

from dataclasses import replace

from quoter.book.book_manager import BookManager
from quoter.config import Config
from quoter.markets import Market
from quoter.quoter_loop import QuoterLoop
from quoter.risk.caps import RiskGuard
from quoter.strategy.inventory import Inventory


def _market(mid: str) -> Market:
    return Market(
        market_id=mid, asset="BTC", timeframe="5m",
        yes_token=f"{mid}-y", no_token=f"{mid}-n",
        slug="btc-updown-5m-1700000000",
        open_ts=1700000000, expire_ts=2000000000,  # far future
    )


class _RecordingExecutor:
    def __init__(self) -> None:
        self.synced: list[str] = []
        self.cancelled: list[str] = []

    def sync(self, market_id, desired):
        self.synced.append(market_id)
        return {"posted": 0, "cancelled": 0, "kept": 0}

    def cancel_all_for_market(self, market_id):
        self.cancelled.append(market_id)
        return 1


def _loop(inv, risk, ex):
    cfg = replace(Config(mode="paper"), max_market_position_usd=50.0)
    return QuoterLoop(
        cfg=cfg, markets=[_market("M1")], book_manager=BookManager(),
        executor=ex, inventory=inv, risk=risk, get_binance_price=lambda _a: None,
    )


class TestPerMarketRiskGate:
    async def test_over_cap_market_is_skipped_not_synced(self):
        cfg = replace(Config(mode="paper"), max_market_position_usd=50.0)
        inv = Inventory()
        inv.on_fill("M1", "YES", 0.50, 200)  # $100 cost > $50 cap
        risk = RiskGuard(cfg, inv)
        ex = _RecordingExecutor()
        loop = QuoterLoop(
            cfg=cfg, markets=[_market("M1")], book_manager=BookManager(),
            executor=ex, inventory=inv, risk=risk, get_binance_price=lambda _a: None,
        )

        await loop._requote_market("M1")

        assert "M1" in ex.cancelled       # quotes pulled for the over-cap market
        assert "M1" not in ex.synced      # but no new ladder posted
        assert not risk.stopped           # bot NOT globally halted
