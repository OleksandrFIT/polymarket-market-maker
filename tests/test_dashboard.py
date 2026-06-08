"""Tests for the HTTP dashboard endpoints."""

from __future__ import annotations

from dataclasses import replace

from aiohttp.test_utils import TestClient, TestServer

from quoter.book.book_manager import BookManager
from quoter.config import Config
from quoter.execution.paper_executor import PaperExecutor
from quoter.markets import Market
from quoter.ops.live_settings import LiveSettings
from quoter.ops.metrics import make_app
from quoter.persistence.state import State
from quoter.quoter_loop import QuoterLoop
from quoter.risk.caps import RiskGuard
from quoter.strategy.inventory import Inventory
from quoter.strategy.ladder import Quote


def _market(mid: str, asset: str = "BTC") -> Market:
    return Market(
        market_id=mid,
        asset=asset,
        timeframe="5m",
        yes_token=f"{mid}-y",
        no_token=f"{mid}-n",
        slug=f"{asset.lower()}-updown-5m-1700000000",
        open_ts=1700000000,
        expire_ts=1700000300,
    )


def _build_app(
    quoter: QuoterLoop,
    state: State | None = None,
    live_settings: LiveSettings | None = None,
) -> object:
    cfg = Config(mode="paper")
    inv = Inventory()
    if live_settings is None:
        live_settings = LiveSettings(cfg)
    return make_app(
        cfg=cfg,
        inventory=inv,
        executor=quoter.exec,
        quoter=quoter,
        risk=RiskGuard(cfg, inv),
        book_manager=quoter.bm,
        state=state,
        live_settings=live_settings,
    )


def _quoter(markets: list[Market]) -> QuoterLoop:
    cfg = Config(mode="paper")
    inv = Inventory()
    bm = BookManager()
    return QuoterLoop(
        cfg=cfg,
        markets=markets,
        book_manager=bm,
        executor=PaperExecutor(),
        inventory=inv,
        risk=RiskGuard(cfg, inv),
        get_binance_price=lambda _a: None,
    )


class TestMarketsEndpoint:
    async def test_reflects_dynamically_added_market(self):
        quoter = _quoter([_market("0xstart")])
        app = _build_app(quoter)
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/api/markets")
            ids_before = {m["market_id"] for m in (await r.json())["markets"]}
            assert ids_before == {"0xstart"}

            quoter.add_market(_market("0xnew"))

            r2 = await client.get("/api/markets")
            ids_after = {m["market_id"] for m in (await r2.json())["markets"]}
            assert "0xnew" in ids_after

    async def test_drops_removed_market(self):
        quoter = _quoter([_market("0xa"), _market("0xb")])
        app = _build_app(quoter)
        async with TestClient(TestServer(app)) as client:
            quoter.remove_market("0xa")
            r = await client.get("/api/markets")
            ids = {m["market_id"] for m in (await r.json())["markets"]}
            assert ids == {"0xb"}


class TestResolvedEndpoint:
    async def test_returns_resolved_markets_from_state(self, tmp_path):
        state = State(str(tmp_path / "d.db"))
        await state.open()
        await state.upsert_market("0xa", "BTC", "5m", 100, 400, "y", "n")
        await state.upsert_market("0xb", "ETH", "5m", 100, 400, "y", "n")
        await state.mark_market_resolved("0xa", "NO", realized_pnl=-3.5)
        try:
            app = _build_app(_quoter([]), state=state)
            async with TestClient(TestServer(app)) as client:
                r = await client.get("/api/resolved")
                data = await r.json()
            rows = data["resolved"]
            assert len(rows) == 1
            assert rows[0]["market_id"] == "0xa"
            assert rows[0]["winning_side"] == "NO"
            assert rows[0]["realized_pnl"] == -3.5
        finally:
            await state.close()

    async def test_empty_when_no_state(self):
        app = _build_app(_quoter([]), state=None)
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/api/resolved")
            data = await r.json()
        assert data == {"resolved": []}


class TestClearEndpoint:
    async def test_clear_resets_state_inventory_executor(self, tmp_path):
        state = State(str(tmp_path / "c.db"))
        await state.open()
        await state.record_fill("M1", "YES", 0.5, 10, source="paper")
        inv = Inventory()
        inv.on_fill("M1", "YES", 0.5, 10)
        ex = PaperExecutor()
        ex.sync("M1", [Quote("YES", 0.5, 10)])
        cfg = Config(mode="paper")
        quoter = _quoter([])
        app = make_app(
            cfg=cfg, inventory=inv, executor=ex, quoter=quoter,
            risk=RiskGuard(cfg, inv), book_manager=quoter.bm, state=state,
            live_settings=LiveSettings(cfg),
        )
        try:
            async with TestClient(TestServer(app)) as client:
                r = await client.post("/api/clear")
                assert (await r.json())["ok"] is True
            assert inv.n_fills == 0
            assert len(inv.positions) == 0
            assert ex.stats()["live_quotes_total"] == 0
            assert await state.n_fills() == 0
        finally:
            await state.close()


class TestRiskEndpoint:
    async def test_sets_limit_to_pct_of_bankroll(self):
        cfg = replace(Config(mode="paper"), bankroll_usd=100.0, max_daily_loss_usd=50.0)
        inv = Inventory()
        risk = RiskGuard(cfg, inv)
        quoter = _quoter([])
        app = make_app(
            cfg=cfg, inventory=inv, executor=quoter.exec, quoter=quoter,
            risk=risk, book_manager=quoter.bm, state=None,
            live_settings=LiveSettings(cfg),
        )
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/api/risk", json={"pct": 100})
            data = await r.json()
        assert data["max_daily_loss_usd"] == 100.0
        assert data["pct"] == 100.0
        assert risk.max_daily_loss_usd == 100.0

    async def test_metrics_exposes_risk_limit(self):
        cfg = replace(Config(mode="paper"), bankroll_usd=100.0, max_daily_loss_usd=50.0)
        inv = Inventory()
        risk = RiskGuard(cfg, inv)
        quoter = _quoter([])
        app = make_app(
            cfg=cfg, inventory=inv, executor=quoter.exec, quoter=quoter,
            risk=risk, book_manager=quoter.bm, state=None,
            live_settings=LiveSettings(cfg),
        )
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/api/metrics")
            risk_block = (await r.json())["risk"]
        assert risk_block["max_daily_loss_usd"] == 50.0
        assert risk_block["pct"] == 50.0


class TestSettingsEndpoint:
    async def test_get_settings(self):
        quoter = _quoter([])
        app = _build_app(quoter)
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/api/settings")
            assert r.status == 200
            data = await r.json()
            assert "per_market_cap_usd" in data and "merge_edge" in data

    async def test_post_settings_applies(self):
        quoter = _quoter([])
        app = _build_app(quoter)
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/api/settings", json={"key": "per_market_cap_usd", "value": 15})
            assert r.status == 200
            g = await (await client.get("/api/settings")).json()
            assert g["per_market_cap_usd"] == 15.0

    async def test_post_settings_bad_400(self):
        quoter = _quoter([])
        app = _build_app(quoter)
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/api/settings", json={"key": "merge_edge", "value": 9})
            assert r.status == 400
            r2 = await client.post("/api/settings", json={"key": "nope", "value": 1})
            assert r2.status == 400
            assert (await client.get("/api/settings")).status == 200

    async def test_post_settings_missing_fields_400(self):
        quoter = _quoter([])
        app = _build_app(quoter)
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/api/settings", json={})
            assert r.status == 400
