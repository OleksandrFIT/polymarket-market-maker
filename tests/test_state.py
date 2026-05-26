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
