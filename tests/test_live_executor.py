"""Tests for LiveExecutor — fill-event-driven repost + sync.

ClobOps is mocked since we can't hit real Polymarket in tests.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from quoter.execution.live_executor import LiveExecutor, LiveOrder
from quoter.strategy.ladder import Quote


@pytest.fixture
def mock_clob():
    """ClobOps with all async methods stubbed."""
    m = MagicMock()
    m.place_limit = AsyncMock(return_value={"order_id": "test_oid_1", "status": "open"})
    m.cancel_orders = AsyncMock(return_value=0)
    m.cancel_order = AsyncMock(return_value=True)
    m.cancel_all = AsyncMock(return_value=0)
    m.get_open_orders = AsyncMock(return_value=[])
    return m


@pytest.fixture
def executor(mock_clob):
    ex = LiveExecutor(clob=mock_clob)
    ex.register_token("token_yes", "M1", "YES")
    ex.register_token("token_no", "M1", "NO")
    return ex


# ── sync() — diff cancel + post ──

@pytest.mark.asyncio
async def test_initial_sync_posts_everything(executor, mock_clob):
    desired = [Quote("YES", 0.50, 10), Quote("NO", 0.30, 5)]
    counts = {"id_count": 0}

    async def fake_place(token_id, price, size, side, post_only):
        counts["id_count"] += 1
        return {"order_id": f"oid_{counts['id_count']}", "status": "open"}

    mock_clob.place_limit.side_effect = fake_place
    result = executor.sync("M1", desired)
    # Wait for async sync task to finish
    await asyncio.sleep(0.05)
    assert result["posted"] == 2
    assert result["cancelled"] == 0
    assert len(executor.live) == 2


@pytest.mark.asyncio
async def test_sync_uses_post_only_flag(executor, mock_clob):
    desired = [Quote("YES", 0.50, 10)]
    executor.sync("M1", desired)
    await asyncio.sleep(0.05)
    args, kwargs = mock_clob.place_limit.call_args
    assert kwargs["post_only"] is True
    assert kwargs["price"] == 0.50
    assert kwargs["size"] == 10


@pytest.mark.asyncio
async def test_sync_diff_cancels_and_posts(executor, mock_clob):
    # First sync: 2 quotes
    counts = {"n": 0}

    async def fake_place(**kw):
        counts["n"] += 1
        return {"order_id": f"oid_{counts['n']}", "status": "open"}

    mock_clob.place_limit.side_effect = fake_place
    executor.sync("M1", [Quote("YES", 0.50, 10), Quote("NO", 0.30, 5)])
    await asyncio.sleep(0.05)
    assert len(executor.live) == 2
    # Second sync: change YES price → 1 cancel + 1 post; NO unchanged
    mock_clob.cancel_orders.return_value = 1
    executor.sync("M1", [Quote("YES", 0.49, 10), Quote("NO", 0.30, 5)])
    await asyncio.sleep(0.05)
    # Only 1 cancel (YES 0.50) + 1 post (YES 0.49); NO kept
    assert mock_clob.cancel_orders.call_count >= 1


# ── Fill-event-driven instant repost ──

@pytest.mark.asyncio
async def test_trade_event_triggers_instant_repost(executor, mock_clob):
    # Pre-populate one live order
    executor.live["oid_1"] = LiveOrder(
        order_id="oid_1", market_id="M1", token_id="token_yes",
        side="YES", price=0.50, size=10, placed_at=0.0,
    )
    mock_clob.place_limit.return_value = {"order_id": "new_oid", "status": "open"}

    fill_trace: list = []

    async def cb(market_id, side, price, qty):
        fill_trace.append((market_id, side, price, qty))

    executor.set_fill_callback(cb)
    await executor.on_user_event({
        "event_type": "trade",
        "order_id": "oid_1",
        "asset_id": "token_yes",
        "price": "0.50",
        "size": "10",
    })
    # Old order removed
    assert "oid_1" not in executor.live
    # Callback fired
    assert fill_trace == [("M1", "YES", 0.50, 10)]
    # Instant repost happened on SAME price/size
    assert mock_clob.place_limit.called
    args, kwargs = mock_clob.place_limit.call_args
    assert kwargs["price"] == 0.50
    assert kwargs["size"] == 10
    assert kwargs["token_id"] == "token_yes"
    # New order tracked
    assert "new_oid" in executor.live


@pytest.mark.asyncio
async def test_partial_fill_reduces_size_not_removes(executor, mock_clob):
    executor.live["oid_1"] = LiveOrder(
        order_id="oid_1", market_id="M1", token_id="token_yes",
        side="YES", price=0.50, size=20, placed_at=0.0,
    )
    mock_clob.place_limit.return_value = {"order_id": "new_oid", "status": "open"}
    await executor.on_user_event({
        "event_type": "trade",
        "order_id": "oid_1",
        "asset_id": "token_yes",
        "price": "0.50",
        "size": "5",  # partial: 5 of 20
    })
    # Original order STILL present but with reduced size
    assert "oid_1" in executor.live
    assert executor.live["oid_1"].size == 15


@pytest.mark.asyncio
async def test_order_canceled_event_removes_from_live(executor):
    executor.live["oid_1"] = LiveOrder(
        order_id="oid_1", market_id="M1", token_id="token_yes",
        side="YES", price=0.50, size=10, placed_at=0.0,
    )
    await executor.on_user_event({
        "event_type": "order",
        "id": "oid_1",
        "status": "CANCELED",
    })
    assert "oid_1" not in executor.live


@pytest.mark.asyncio
async def test_trade_for_unknown_token_ignored(executor, mock_clob):
    await executor.on_user_event({
        "event_type": "trade",
        "asset_id": "unknown_token",
        "price": "0.50",
        "size": "10",
    })
    # No repost attempted
    mock_clob.place_limit.assert_not_called()


# ── Lifecycle ──

@pytest.mark.asyncio
async def test_cancel_all_for_market(executor, mock_clob):
    executor.live = {
        "a": LiveOrder("a", "M1", "token_yes", "YES", 0.5, 10, 0),
        "b": LiveOrder("b", "M1", "token_no", "NO", 0.4, 10, 0),
        "c": LiveOrder("c", "M2", "tok2", "YES", 0.5, 10, 0),
    }
    mock_clob.cancel_orders.return_value = 2
    n = executor.cancel_all_for_market("M1")
    assert n == 2
    await asyncio.sleep(0.05)
    # Only M2 left
    assert set(executor.live.keys()) == {"c"}


@pytest.mark.asyncio
async def test_startup_cancel_all_open(executor, mock_clob):
    mock_clob.cancel_all.return_value = 42
    executor.live["a"] = LiveOrder("a", "M1", "tok", "YES", 0.5, 10, 0)
    n = await executor.cancel_all_open()
    assert n == 42
    assert executor.live == {}


# ── Metrics ──

def test_stats_returns_all_counters(executor):
    s = executor.stats()
    for key in (
        "sync_count", "cumulative_posts", "cumulative_cancels",
        "cumulative_fills", "cumulative_instant_reposts",
        "failed_posts", "live_markets", "live_quotes_total",
    ):
        assert key in s
