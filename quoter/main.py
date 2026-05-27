"""poly-quoter entrypoint.

Phase 4 scope: SHADOW + PAPER modes.

  MODE=shadow  → ShadowExecutor   (no fills, log diff only)
  MODE=paper   → PaperExecutor    (simulated fills via book traversal,
                                   inventory updates, SQLite persistence)
  MODE=live    → not yet wired    (Phase 5)

Run with: ``uv run python -m quoter.main``
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Any

import uvloop
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

uvloop.install()

from quoter.book.book_manager import BookManager  # noqa: E402
from quoter.config import Config  # noqa: E402
from quoter.execution.paper_executor import PaperExecutor  # noqa: E402
from quoter.execution.shadow_executor import ShadowExecutor  # noqa: E402
from quoter.feeds.binance_ws import BinanceWS  # noqa: E402
from quoter.feeds.poly_market_ws import PolyMarketWS  # noqa: E402
from quoter.lifecycle.market_lifecycle import MarketLifecycle  # noqa: E402
from quoter.markets import Market, discover_markets  # noqa: E402
from quoter.ops.logger import get_logger, setup_logging  # noqa: E402
from quoter.ops.metrics import make_app, serve_forever  # noqa: E402
from quoter.persistence.state import State  # noqa: E402
from quoter.quoter_loop import QuoterLoop  # noqa: E402
from quoter.risk.caps import RiskGuard  # noqa: E402
from quoter.strategy.inventory import Inventory  # noqa: E402


def _build_executor(mode: str) -> Any:
    if mode == "shadow":
        return ShadowExecutor()
    if mode == "paper":
        return PaperExecutor()
    raise NotImplementedError(f"Live mode wired in Phase 5; got {mode!r}")


async def _periodic_snapshot(
    log: Any,
    inv: Inventory,
    executor: Any,
    loop: QuoterLoop,
    state: State | None,
    interval_sec: float = 30.0,
) -> None:
    while True:
        await asyncio.sleep(interval_sec)
        snap = inv.snapshot()
        log.info("snapshot", inventory=snap, executor=executor.stats(), quoter=loop.stats())
        # Persist positions
        if state is not None:
            for mid, p in inv.positions.items():
                await state.upsert_position(
                    mid, p.yes_qty, p.no_qty, p.yes_cost_total, p.no_cost_total
                )


async def _persist_fills_loop(state: State, inv: Inventory) -> None:
    """Watch ``inv.n_fills``; whenever it grows, dump new fills to SQLite.

    Simple polling (1Hz) since fills come in via callbacks we'd need to
    wire deeper otherwise; for paper at ~10-100 fills/min this is fine.
    """
    last_n = inv.n_fills
    while True:
        await asyncio.sleep(1.0)
        if inv.n_fills > last_n:
            # We don't have per-fill history here; positions table reflects
            # current state. For richer post-mortem we'd hook PaperExecutor.
            last_n = inv.n_fills


async def _await_shutdown(tasks: list[asyncio.Task]) -> None:
    stop = asyncio.Event()
    runtime_loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        runtime_loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _init_state(cfg: Config, markets: list[Market]) -> tuple[State | None, float]:
    """Open SQLite, start session, persist market metadata. Paper mode only."""
    if cfg.mode != "paper":
        return None, 0.0
    state = State(cfg.db_path)
    await state.open()
    session_ts = await state.start_session(cfg.mode, cfg.bankroll_usd)
    for m in markets:
        await state.upsert_market(
            m.market_id, m.asset, m.timeframe,
            m.open_ts, m.expire_ts,
            m.yes_token, m.no_token,
        )
    return state, session_ts


async def _amain() -> None:  # noqa: C901  (entry-point orchestration, hard to split further)
    cfg = Config.from_env()
    setup_logging(cfg.log_path, cfg.log_level)
    log = get_logger("main")
    log.info("startup", mode=cfg.mode, bankroll=cfg.bankroll_usd)

    if cfg.mode == "live":
        log.error("live_mode_not_yet_wired_phase_5")
        return

    markets = await discover_markets(cfg)
    if not markets:
        log.warning("no_markets_at_startup_will_keep_polling")
    for m in markets:
        log.info(
            "market_active",
            asset=m.asset, tf=m.timeframe,
            condition=m.market_id[:12], expires_in=int(m.time_remaining()),
        )

    state, session_ts = await _init_state(cfg, markets)
    book_manager = BookManager()
    inventory = Inventory()
    executor = _build_executor(cfg.mode)
    risk = RiskGuard(cfg, inventory)
    binance_latest: dict[str, float] = {}

    # Fill persistence hook (only in paper / live)
    async def on_fill(market_id: str, side: str, price: float, qty: int) -> None:
        if state is not None:
            await state.record_fill(market_id, side, price, qty, source=cfg.mode)

    quoter = QuoterLoop(
        cfg=cfg, markets=markets, book_manager=book_manager,
        executor=executor, inventory=inventory, risk=risk,
        get_binance_price=binance_latest.get,
        on_fill=on_fill,
    )
    # Subscribe listener for any token added later via MarketLifecycle.
    # We subscribe per-token on first event arrival lazily — the listener
    # is the same closure for every token. Simpler: register for current
    # tokens at startup and re-register when new markets land.
    book_listener = _make_book_listener(quoter)
    for token in quoter.known_tokens():
        book_manager.subscribe(token, book_listener)

    async def on_market_added(m: Market) -> None:
        """Hook from MarketLifecycle when a new market joins tracking."""
        book_manager.subscribe(m.yes_token, book_listener)
        book_manager.subscribe(m.no_token, book_listener)

    async def on_btc_price(asset: str, price: float, _ts: float) -> None:
        binance_latest[asset] = price
        quoter.mark_dirty_by_asset(asset)

    async def on_poly_event(msg: dict) -> None:
        await book_manager.on_ws_event(msg)

    binance = BinanceWS(cfg.assets, on_btc_price)
    poly = PolyMarketWS(cfg.ws_market_url, on_poly_event)
    poly.set_subscriptions(quoter.known_tokens())

    lifecycle = MarketLifecycle(
        cfg=cfg, quoter=quoter, executor=executor, poly_ws=poly,
        state=state, inventory=inventory,
        on_market_added=on_market_added, interval_sec=30,
    )

    # HTTP dashboard
    dashboard_app = make_app(
        cfg=cfg, inventory=inventory, executor=executor, quoter=quoter,
        risk=risk, book_manager=book_manager, state=state,
        session_ts=session_ts,
    )

    tasks = [
        asyncio.create_task(binance.run(), name="binance_ws"),
        asyncio.create_task(poly.run(), name="poly_market_ws"),
        asyncio.create_task(quoter.run(), name="quoter_loop"),
        asyncio.create_task(lifecycle.run(), name="market_lifecycle"),
        asyncio.create_task(
            _periodic_snapshot(log, inventory, executor, quoter, state),
            name="snapshot_loop",
        ),
        asyncio.create_task(
            serve_forever(dashboard_app, host="127.0.0.1", port=8080),
            name="dashboard_http",
        ),
    ]
    log.info("running_tasks_started", tasks=[t.get_name() for t in tasks])
    await _await_shutdown(tasks)

    # ── Shutdown ──
    if state is not None:
        await state.end_session(session_ts, inventory.realized_pnl)
        await state.close()
    log.info(
        "shutdown_complete",
        final_inventory=inventory.snapshot(),
        executor_final=executor.stats(),
    )


def _make_book_listener(quoter: QuoterLoop) -> Any:
    async def listener(token_id: str) -> None:
        quoter.mark_dirty_by_token(token_id)
    return listener


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
