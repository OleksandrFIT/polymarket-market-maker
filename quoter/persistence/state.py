"""SQLite state persistence: fills, positions, markets.

Single-file DB at ``cfg.db_path``. WAL mode for concurrent reads. Schema is
3 tables — no ORM, no migrations beyond ``CREATE TABLE IF NOT EXISTS``.

Used in paper and live modes for post-mortem analysis: ``analyze_session.py``
script reads this DB and produces fill histograms, P&L curves, etc.
"""

from __future__ import annotations

import time
from typing import Any

import aiosqlite

from quoter.ops.logger import get_logger

log = get_logger("state")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    ts          REAL    NOT NULL,
    market_id   TEXT    NOT NULL,
    side        TEXT    NOT NULL,    -- 'YES' | 'NO'
    price       REAL    NOT NULL,
    qty         INTEGER NOT NULL,
    cost        REAL    NOT NULL,
    source      TEXT    NOT NULL     -- 'paper' | 'live'
);
CREATE INDEX IF NOT EXISTS idx_fills_market ON fills(market_id);
CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(ts);

CREATE TABLE IF NOT EXISTS positions (
    market_id   TEXT PRIMARY KEY,
    yes_qty     INTEGER NOT NULL,
    no_qty      INTEGER NOT NULL,
    yes_cost    REAL NOT NULL,
    no_cost     REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS markets (
    market_id    TEXT PRIMARY KEY,
    asset        TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    open_ts      INTEGER NOT NULL,
    expire_ts    INTEGER NOT NULL,
    strike       REAL DEFAULT 0,
    status       TEXT DEFAULT 'OPEN',  -- OPEN | CLOSED | RESOLVED
    winning_side TEXT,                  -- 'YES' | 'NO' | NULL
    resolved_pnl REAL,                  -- realized P&L at resolution
    resolved_at  REAL,                  -- unix ts of resolution
    yes_token    TEXT,
    no_token     TEXT,
    discovered_at REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    started_at   REAL PRIMARY KEY,
    mode         TEXT NOT NULL,
    bankroll     REAL NOT NULL,
    ended_at     REAL,
    final_pnl    REAL
);
"""


class State:
    """Async SQLite wrapper. Open on startup, close on shutdown."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA cache_size=-64000")  # 64MB
        await self._db.executescript(_SCHEMA)
        await self._migrate()
        await self._db.commit()
        log.info("state_opened", path=self._path)

    async def _migrate(self) -> None:
        """Additive column migrations for DBs created by older schema versions."""
        for ddl in (
            "ALTER TABLE markets ADD COLUMN resolved_pnl REAL",
            "ALTER TABLE markets ADD COLUMN resolved_at REAL",
        ):
            try:
                await self.db.execute(ddl)
            except Exception:
                pass  # column already present

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            log.info("state_closed")

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("State not opened — call .open() first")
        return self._db

    # ── Fills ──

    async def record_fill(
        self,
        market_id: str,
        side: str,
        price: float,
        qty: int,
        source: str = "paper",
        ts: float | None = None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO fills(ts, market_id, side, price, qty, cost, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts or time.time(), market_id, side, price, qty, price * qty, source),
        )
        await self.db.commit()

    async def fills_for_market(self, market_id: str) -> list[tuple[Any, ...]]:
        async with self.db.execute(
            "SELECT ts, side, price, qty, cost FROM fills WHERE market_id = ? ORDER BY ts",
            (market_id,),
        ) as cur:
            return await cur.fetchall()

    async def n_fills(self) -> int:
        async with self.db.execute("SELECT COUNT(*) FROM fills") as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    # ── Positions ──

    async def upsert_position(
        self,
        market_id: str,
        yes_qty: int,
        no_qty: int,
        yes_cost: float,
        no_cost: float,
    ) -> None:
        await self.db.execute(
            "INSERT INTO positions(market_id, yes_qty, no_qty, yes_cost, no_cost, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(market_id) DO UPDATE SET "
            "  yes_qty=excluded.yes_qty, no_qty=excluded.no_qty, "
            "  yes_cost=excluded.yes_cost, no_cost=excluded.no_cost, "
            "  updated_at=excluded.updated_at",
            (market_id, yes_qty, no_qty, yes_cost, no_cost, time.time()),
        )
        await self.db.commit()

    async def load_positions(self) -> list[tuple[Any, ...]]:
        async with self.db.execute(
            "SELECT market_id, yes_qty, no_qty, yes_cost, no_cost FROM positions"
        ) as cur:
            return await cur.fetchall()

    # ── Markets ──

    async def upsert_market(
        self,
        market_id: str,
        asset: str,
        timeframe: str,
        open_ts: int,
        expire_ts: int,
        yes_token: str,
        no_token: str,
        strike: float = 0.0,
    ) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO markets"
            "(market_id, asset, timeframe, open_ts, expire_ts, strike, yes_token, no_token, discovered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (market_id, asset, timeframe, open_ts, expire_ts, strike, yes_token, no_token, time.time()),
        )
        await self.db.commit()

    async def mark_market_resolved(
        self, market_id: str, winning_side: str, realized_pnl: float = 0.0
    ) -> None:
        await self.db.execute(
            "UPDATE markets SET status='RESOLVED', winning_side=?, "
            "resolved_pnl=?, resolved_at=? WHERE market_id=?",
            (winning_side, realized_pnl, time.time(), market_id),
        )
        await self.db.commit()

    async def resolved_markets(self) -> list[tuple[Any, ...]]:
        """All resolved markets, newest resolution first.

        Rows: ``(market_id, asset, timeframe, winning_side, resolved_pnl, resolved_at)``.
        """
        async with self.db.execute(
            "SELECT market_id, asset, timeframe, winning_side, resolved_pnl, resolved_at "
            "FROM markets WHERE status='RESOLVED' "
            "ORDER BY resolved_at DESC, rowid DESC"
        ) as cur:
            return await cur.fetchall()

    async def clear_all(self) -> None:
        """Delete all rows from every table — operator 'clear all data' action."""
        for table in ("fills", "positions", "markets", "sessions"):
            await self.db.execute(f"DELETE FROM {table}")
        await self.db.commit()
        log.warning("state_cleared_all")

    # ── Sessions ──

    async def start_session(self, mode: str, bankroll: float) -> float:
        ts = time.time()
        await self.db.execute(
            "INSERT INTO sessions(started_at, mode, bankroll) VALUES (?, ?, ?)",
            (ts, mode, bankroll),
        )
        await self.db.commit()
        return ts

    async def end_session(self, session_ts: float, final_pnl: float) -> None:
        await self.db.execute(
            "UPDATE sessions SET ended_at=?, final_pnl=? WHERE started_at=?",
            (time.time(), final_pnl, session_ts),
        )
        await self.db.commit()
