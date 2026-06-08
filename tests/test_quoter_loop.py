"""Tests for QuoterLoop per-market risk gating."""

from __future__ import annotations

from dataclasses import replace

from quoter.book.book_manager import BookManager
from quoter.config import Config
from quoter.markets import Market
from quoter.quoter_loop import QuoterLoop
from quoter.risk.caps import RiskGuard
from quoter.strategy.inventory import Inventory
from quoter.ops.live_settings import LiveSettings
from quoter.execution.paper_executor import PaperExecutor, FillEvent


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


class _FillingExecutor(PaperExecutor):
    """PaperExecutor whose check_fills returns a fixed list of fills."""

    def __init__(self, fills):
        super().__init__()
        self._fixed = fills

    def check_fills(self, market_id, yes_top, no_top):
        return self._fixed


class TestPaperMerge:
    async def test_apply_paper_fills_merges_matched(self):
        cfg = replace(Config(mode="paper"), max_market_position_usd=1e9)
        inv = Inventory()
        risk = RiskGuard(cfg, inv)
        ex = _FillingExecutor([
            FillEvent("M1", "YES", 0.55, 10),
            FillEvent("M1", "NO", 0.44, 10),
        ])
        loop = QuoterLoop(
            cfg=cfg, markets=[_market("M1")], book_manager=BookManager(),
            executor=ex, inventory=inv, risk=risk, get_binance_price=lambda _a: None,
        )

        await loop._apply_paper_fills("M1", None, None)

        # 10 YES + 10 NO -> 10 matched pairs merged -> both legs cleared.
        assert inv.n_merges >= 1
        pos = inv.positions.get("M1")
        assert pos is None or (pos.yes_qty == 0 and pos.no_qty == 0)
        # Locked spread = 10 * (1 - (0.55 + 0.44)) = 0.10
        assert abs(inv.realized_pnl - 0.10) < 1e-9

    async def test_naked_fill_not_merged(self):
        cfg = replace(Config(mode="paper"), max_market_position_usd=1e9)
        inv = Inventory()
        risk = RiskGuard(cfg, inv)
        ex = _FillingExecutor([FillEvent("M1", "YES", 0.55, 10)])  # one leg only
        loop = QuoterLoop(
            cfg=cfg, markets=[_market("M1")], book_manager=BookManager(),
            executor=ex, inventory=inv, risk=risk, get_binance_price=lambda _a: None,
        )

        await loop._apply_paper_fills("M1", None, None)

        # No NO leg -> nothing matched -> no merge; naked YES held.
        assert inv.n_merges == 0
        assert inv.positions["M1"].yes_qty == 10
        assert inv.positions["M1"].no_qty == 0


class TestLiveSettingsOverlay:
    def test_live_settings_overlay_applies(self):
        """LiveSettings.update mutates the overlay; dataclasses.replace applies it
        to a fresh eff_cfg without touching the frozen base Config."""
        inv = Inventory()
        risk = RiskGuard(replace(Config(mode="paper"), max_market_position_usd=50.0), inv)
        ex = _RecordingExecutor()
        loop = _loop(inv, risk, ex)

        # Sanity: base value must differ from 15 so the test is meaningful.
        assert loop.cfg.per_market_cap_usd != 15.0

        loop.live.update("per_market_cap_usd", 15)
        eff = replace(loop.cfg, **loop.live.snapshot())

        assert eff.per_market_cap_usd == 15.0
        assert loop.cfg.per_market_cap_usd != 15.0  # base config must remain unchanged
