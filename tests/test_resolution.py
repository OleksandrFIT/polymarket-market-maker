"""Tests for Polymarket resolution polling + lifecycle settlement."""

from __future__ import annotations

from quoter.config import Config
from quoter.lifecycle.market_lifecycle import MarketLifecycle
from quoter.lifecycle.resolution import fetch_resolution
from quoter.markets import Market
from quoter.strategy.inventory import Inventory


def _market(mid: str = "0xabc", yes: str = "111", no: str = "222") -> Market:
    return Market(
        market_id=mid,
        asset="BTC",
        timeframe="5m",
        yes_token=yes,
        no_token=no,
        slug="btc-updown-5m-1700000000",
        open_ts=1700000000,
        expire_ts=1700000300,
    )


class _FakeResp:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Returns a canned response; records the URL it was asked for."""

    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp
        self.requested_url: str | None = None

    async def get(self, url: str) -> _FakeResp:
        self.requested_url = url
        return self._resp


def _resolved_payload(*, closed: bool, winner_token: str | None) -> dict:
    tokens = [
        {"token_id": "111", "outcome": "Up", "price": 0, "winner": False},
        {"token_id": "222", "outcome": "Down", "price": 0, "winner": False},
    ]
    for t in tokens:
        if winner_token is not None and t["token_id"] == winner_token:
            t["winner"] = True
            t["price"] = 1
    return {"closed": closed, "tokens": tokens}


class TestFetchResolution:
    async def test_winner_on_no_token_returns_no(self):
        client = _FakeClient(_FakeResp(200, _resolved_payload(closed=True, winner_token="222")))
        side = await fetch_resolution(client, "https://clob.example", _market())
        assert side == "NO"

    async def test_winner_on_yes_token_returns_yes(self):
        client = _FakeClient(_FakeResp(200, _resolved_payload(closed=True, winner_token="111")))
        side = await fetch_resolution(client, "https://clob.example", _market())
        assert side == "YES"

    async def test_not_closed_returns_none(self):
        client = _FakeClient(_FakeResp(200, _resolved_payload(closed=False, winner_token=None)))
        side = await fetch_resolution(client, "https://clob.example", _market())
        assert side is None

    async def test_closed_without_winner_returns_none(self):
        client = _FakeClient(_FakeResp(200, _resolved_payload(closed=True, winner_token=None)))
        side = await fetch_resolution(client, "https://clob.example", _market())
        assert side is None

    async def test_winner_token_matching_neither_returns_none(self):
        payload = {"closed": True, "tokens": [{"token_id": "999", "winner": True}]}
        client = _FakeClient(_FakeResp(200, payload))
        side = await fetch_resolution(client, "https://clob.example", _market())
        assert side is None

    async def test_non_200_returns_none(self):
        client = _FakeClient(_FakeResp(404, {}))
        side = await fetch_resolution(client, "https://clob.example", _market())
        assert side is None

    async def test_queries_clob_market_endpoint(self):
        client = _FakeClient(_FakeResp(200, _resolved_payload(closed=True, winner_token="222")))
        await fetch_resolution(client, "https://clob.example", _market(mid="0xdeadbeef"))
        assert client.requested_url == "https://clob.example/markets/0xdeadbeef"


class _FakeQuoter:
    def __init__(self, markets: dict[str, Market]) -> None:
        self.markets = markets

    def remove_market(self, market_id: str) -> Market | None:
        return self.markets.pop(market_id, None)


class _FakeExecutor:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def cancel_all_for_market(self, market_id: str) -> int:
        self.cancelled.append(market_id)
        return 0


class _FakeState:
    def __init__(self) -> None:
        self.resolved: list[tuple[str, str]] = []

    async def mark_market_resolved(
        self, market_id: str, winning_side: str, realized_pnl: float = 0.0
    ) -> None:
        self.resolved.append((market_id, winning_side))


def _lifecycle(inv: Inventory, quoter: _FakeQuoter, state: _FakeState | None) -> MarketLifecycle:
    return MarketLifecycle(
        cfg=Config(mode="paper"),
        quoter=quoter,  # type: ignore[arg-type]
        executor=_FakeExecutor(),
        poly_ws=object(),  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        inventory=inv,
    )


class TestLifecycleResolution:
    async def test_handle_expired_keeps_position_and_moves_to_pending(self):
        m = _market()
        inv = Inventory()
        inv.on_fill(m.market_id, "NO", 0.40, 100)
        quoter = _FakeQuoter({m.market_id: m})
        lc = _lifecycle(inv, quoter, None)

        await lc._handle_expired(m.market_id)

        # Position must NOT be settled yet — we wait for real resolution
        assert m.market_id in inv.positions
        assert inv.n_resolutions == 0
        # Market is removed from quoting but tracked for resolution
        assert m.market_id not in quoter.markets
        assert m.market_id in lc._pending

    async def test_poll_resolves_pending_and_settles_inventory(self):
        m = _market()
        inv = Inventory()
        inv.on_fill(m.market_id, "NO", 0.40, 100)  # $40 cost, NO wins → $100
        quoter = _FakeQuoter({})
        state = _FakeState()
        lc = _lifecycle(inv, quoter, state)
        lc._pending[m.market_id] = m

        client = _FakeClient(_FakeResp(200, _resolved_payload(closed=True, winner_token="222")))
        await lc._poll_resolutions(client)

        assert inv.n_resolutions == 1
        assert inv.realized_pnl == 60.0  # 100 - 40
        assert m.market_id not in inv.positions
        assert m.market_id not in lc._pending
        assert state.resolved == [(m.market_id, "NO")]

    async def test_poll_leaves_unresolved_market_pending(self):
        m = _market()
        inv = Inventory()
        inv.on_fill(m.market_id, "YES", 0.50, 10)
        lc = _lifecycle(inv, _FakeQuoter({}), None)
        lc._pending[m.market_id] = m

        client = _FakeClient(_FakeResp(200, _resolved_payload(closed=False, winner_token=None)))
        await lc._poll_resolutions(client)

        assert inv.n_resolutions == 0
        assert m.market_id in lc._pending
