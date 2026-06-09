"""Tests for the control dashboard endpoints (no live runner)."""

from aiohttp.test_utils import TestClient, TestServer

from quoter.runner.trading_state import TradingState
from quoter.runner.control_dashboard import make_control_app


def _client_app():
    state = TradingState()
    return state, make_control_app(state, runner=None)


class TestControlEndpoints:
    async def test_status_default_stopped(self):
        state, app = _client_app()
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/api/status")
            assert r.status == 200
            d = await r.json()
            assert d["mode"] == "STOPPED"
            assert "window" in d

    async def test_start_sets_running(self):
        state, app = _client_app()
        async with TestClient(TestServer(app)) as c:
            r = await c.post("/api/start")
            assert r.status == 200
            assert (await r.json())["mode"] == "RUNNING"
            assert state.mode == "RUNNING"
            assert state.trade_from_open_ts is not None  # current window recorded

    async def test_stop_sets_stopped(self):
        state, app = _client_app()
        async with TestClient(TestServer(app)) as c:
            await c.post("/api/start")
            r = await c.post("/api/stop")
            assert (await r.json())["mode"] == "STOPPED"
            assert state.mode == "STOPPED"

    async def test_force_stop_sets_flag(self):
        state, app = _client_app()
        async with TestClient(TestServer(app)) as c:
            await c.post("/api/start")
            r = await c.post("/api/force_stop")
            assert (await r.json())["mode"] == "STOPPED"
            assert state.force_stop_requested is True

    async def test_root_serves_html(self):
        _, app = _client_app()
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/")
            assert r.status == 200
            assert "merge-maker control" in (await r.text())
