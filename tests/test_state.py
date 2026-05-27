"""Tests for SQLite State persistence."""

from __future__ import annotations

import pytest

from quoter.persistence.state import State


@pytest.fixture
async def state(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = State(db_path)
    await s.open()
    yield s
    await s.close()


async def test_open_creates_schema(state):
    # If we can record a fill, the schema is in place
    await state.record_fill("M1", "YES", 0.5, 10, source="paper")
    assert await state.n_fills() == 1


async def test_records_multiple_fills(state):
    await state.record_fill("M1", "YES", 0.50, 10, source="paper")
    await state.record_fill("M1", "NO", 0.45, 5, source="paper")
    await state.record_fill("M2", "YES", 0.60, 20, source="paper")
    assert await state.n_fills() == 3
    m1_fills = await state.fills_for_market("M1")
    assert len(m1_fills) == 2


async def test_upsert_position_overwrites(state):
    await state.upsert_position("M1", yes_qty=10, no_qty=5, yes_cost=4.0, no_cost=2.5)
    await state.upsert_position("M1", yes_qty=20, no_qty=10, yes_cost=8.0, no_cost=5.0)
    rows = await state.load_positions()
    assert len(rows) == 1
    _, yes_qty, no_qty, *_ = rows[0]
    assert yes_qty == 20
    assert no_qty == 10


async def test_session_lifecycle(state):
    ts = await state.start_session("paper", 100.0)
    assert ts > 0
    await state.end_session(ts, final_pnl=15.5)
    # Sanity: nothing crashed; concrete query helpers not added yet


class TestResolvedMarkets:
    async def test_mark_resolved_records_winner_and_pnl(self, state):
        await state.upsert_market("M1", "BTC", "5m", 100, 400, "y", "n")
        await state.mark_market_resolved("M1", "NO", realized_pnl=12.5)
        rows = await state.resolved_markets()
        assert len(rows) == 1
        market_id, asset, timeframe, winning_side, pnl, _resolved_at = rows[0]
        assert market_id == "M1"
        assert asset == "BTC"
        assert timeframe == "5m"
        assert winning_side == "NO"
        assert pnl == 12.5

    async def test_resolved_markets_excludes_open(self, state):
        await state.upsert_market("M1", "BTC", "5m", 100, 400, "y", "n")  # stays OPEN
        assert await state.resolved_markets() == []

    async def test_resolved_markets_newest_first(self, state):
        await state.upsert_market("M1", "BTC", "5m", 100, 400, "y", "n")
        await state.upsert_market("M2", "ETH", "5m", 100, 400, "y", "n")
        await state.mark_market_resolved("M1", "YES", realized_pnl=1.0)
        await state.mark_market_resolved("M2", "NO", realized_pnl=2.0)
        rows = await state.resolved_markets()
        assert [r[0] for r in rows] == ["M2", "M1"]


class TestClearAll:
    async def test_clear_all_empties_tables(self, state):
        await state.record_fill("M1", "YES", 0.5, 10, source="paper")
        await state.upsert_position("M1", 10, 0, 5.0, 0.0)
        await state.upsert_market("M1", "BTC", "5m", 100, 400, "y", "n")
        await state.start_session("paper", 100.0)
        await state.clear_all()
        assert await state.n_fills() == 0
        assert await state.load_positions() == []
        assert await state.resolved_markets() == []
        async with state.db.execute("SELECT COUNT(*) FROM markets") as cur:
            assert (await cur.fetchone())[0] == 0
        async with state.db.execute("SELECT COUNT(*) FROM sessions") as cur:
            assert (await cur.fetchone())[0] == 0
