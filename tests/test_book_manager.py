"""Tests for BookManager — routing of WS events to per-token books."""

from __future__ import annotations

import pytest

from quoter.book.book_manager import BookManager


@pytest.mark.asyncio
async def test_routes_book_event_to_correct_token():
    bm = BookManager()
    await bm.on_ws_event({
        "event_type": "book",
        "asset_id": "token-A",
        "bids": [{"price": "0.60", "size": "100"}],
        "asks": [{"price": "0.65", "size": "50"}],
        "timestamp": "1700000000000",
    })
    top = bm.top("token-A")
    assert top is not None
    assert top.bid_px == 0.60
    assert top.ask_px == 0.65
    # Other tokens have nothing
    assert bm.top("token-B") is None


@pytest.mark.asyncio
async def test_market_field_alias_for_asset_id():
    bm = BookManager()
    # Some Polymarket events use 'market' instead of 'asset_id'
    bm.book("token-X")  # pre-create so on_ws_event accepts it
    await bm.on_ws_event({
        "event_type": "book",
        "market": "token-X",
        "bids": [{"price": "0.5", "size": "1"}],
        "asks": [],
    })
    assert bm.top("token-X").bid_px == 0.5


@pytest.mark.asyncio
async def test_price_change_applies_delta():
    bm = BookManager()
    await bm.on_ws_event({
        "event_type": "book",
        "asset_id": "tok",
        "bids": [{"price": "0.60", "size": "100"}],
        "asks": [{"price": "0.65", "size": "50"}],
    })
    await bm.on_ws_event({
        "event_type": "price_change",
        "asset_id": "tok",
        "price_changes": [{"price": "0.62", "size": "30", "side": "BUY"}],
    })
    assert bm.top("tok").bid_px == 0.62


@pytest.mark.asyncio
async def test_listener_called_on_book_update():
    bm = BookManager()
    fired: list[str] = []

    async def listener(token_id: str) -> None:
        fired.append(token_id)

    bm.subscribe("tok", listener)
    await bm.on_ws_event({
        "event_type": "book",
        "asset_id": "tok",
        "bids": [{"price": "0.5", "size": "1"}],
        "asks": [],
    })
    assert fired == ["tok"]


@pytest.mark.asyncio
async def test_listener_isolated_per_token():
    bm = BookManager()
    fired_a: list[str] = []
    fired_b: list[str] = []
    bm.subscribe("A", lambda t: _append(fired_a, t))
    bm.subscribe("B", lambda t: _append(fired_b, t))
    await bm.on_ws_event({"event_type": "book", "asset_id": "A", "bids": [], "asks": []})
    await bm.on_ws_event({"event_type": "book", "asset_id": "B", "bids": [], "asks": []})
    assert fired_a == ["A"]
    assert fired_b == ["B"]


async def _append(lst: list[str], item: str) -> None:
    lst.append(item)


@pytest.mark.asyncio
async def test_unknown_event_type_is_silent():
    bm = BookManager()
    bm.book("tok")
    # Should not raise; book remains empty so top() returns None
    await bm.on_ws_event({"event_type": "weird_new_event", "asset_id": "tok"})
    assert bm.top("tok") is None


@pytest.mark.asyncio
async def test_event_without_asset_id_ignored():
    bm = BookManager()
    await bm.on_ws_event({"event_type": "book", "bids": [], "asks": []})
    # No state should change, no error
    assert bm.top("anything") is None
