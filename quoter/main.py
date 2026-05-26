"""poly-quoter entrypoint.

Phase 3 scope: full end-to-end SHADOW mode.

  WS feeds → BookManager → QuoterLoop → ShadowExecutor (logs only)

In MODE=shadow no real orders are placed. Strategy + book + inventory
all exercised end-to-end so we can validate timing, correctness, and
log output before enabling PAPER (Phase 4) or LIVE (Phase 5).

Run with: ``uv run python -m quoter.main``
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import uvloop
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

uvloop.install()

from quoter.book.book_manager import BookManager  # noqa: E402
from quoter.config import Config  # noqa: E402
from quoter.execution.shadow_executor import ShadowExecutor  # noqa: E402
from quoter.feeds.binance_ws import BinanceWS  # noqa: E402
from quoter.feeds.poly_market_ws import PolyMarketWS  # noqa: E402
from quoter.markets import Market, discover_markets  # noqa: E402
from quoter.ops.logger import get_logger, setup_logging  # noqa: E402
from quoter.quoter_loop import QuoterLoop  # noqa: E402
from quoter.risk.caps import RiskGuard  # noqa: E402
from quoter.strategy.inventory import Inventory  # noqa: E402


async def _periodic_snapshot(
    log,
    inv: Inventory,
    executor: ShadowExecutor,
    loop: QuoterLoop,
    interval_sec: float = 30.0,
) -> None:
    """Emit a state snapshot every N seconds for observability."""
    while True:
        await asyncio.sleep(interval_sec)
        log.info(
            "snapshot",
            inventory=inv.snapshot(),
            executor=executor.stats(),
            quoter=loop.stats(),
        )


async def _await_shutdown(tasks: list[asyncio.Task]) -> None:
    stop = asyncio.Event()
    runtime_loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        runtime_loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _amain() -> None:
    cfg = Config.from_env()
    setup_logging(cfg.log_path, cfg.log_level)
    log = get_logger("main")
    log.info("startup", mode=cfg.mode, bankroll=cfg.bankroll_usd)

    if cfg.mode != "shadow":
        log.error("only_shadow_mode_supported_in_phase_3", mode=cfg.mode)
        return

    markets = await discover_markets(cfg)
    if not markets:
        log.error("no_markets_discovered_exiting")
        return
    by_token: dict[str, Market] = {}
    for m in markets:
        by_token[m.yes_token] = m
        by_token[m.no_token] = m
        log.info(
            "market_active",
            asset=m.asset, tf=m.timeframe,
            condition=m.market_id[:12], expires_in=int(m.time_remaining()),
        )

    # ── Wire components ──
    book_manager = BookManager()
    inventory = Inventory()
    executor = ShadowExecutor()
    risk = RiskGuard(cfg, inventory)

    # Binance price latest cache
    binance_latest: dict[str, float] = {}

    def get_binance(asset: str) -> float | None:
        return binance_latest.get(asset)

    quoter = QuoterLoop(
        cfg=cfg,
        markets=markets,
        book_manager=book_manager,
        executor=executor,
        inventory=inventory,
        risk=risk,
        get_binance_price=get_binance,
    )

    # Subscribe BookManager listeners → quoter dirty-marking
    for token in by_token:
        book_manager.subscribe(token, _make_book_listener(quoter))

    # ── Feed callbacks ──
    async def on_btc_price(asset: str, price: float, ts: float) -> None:
        binance_latest[asset] = price
        quoter.mark_dirty_by_asset(asset)

    async def on_poly_event(msg: dict) -> None:
        await book_manager.on_ws_event(msg)

    # ── Build feeds + start tasks ──
    binance = BinanceWS(cfg.assets, on_btc_price)
    poly = PolyMarketWS(cfg.ws_market_url, on_poly_event)
    poly.set_subscriptions(list(by_token.keys()))

    tasks = [
        asyncio.create_task(binance.run(), name="binance_ws"),
        asyncio.create_task(poly.run(), name="poly_market_ws"),
        asyncio.create_task(quoter.run(), name="quoter_loop"),
        asyncio.create_task(_periodic_snapshot(log, inventory, executor, quoter),
                            name="snapshot_loop"),
    ]
    log.info("running_tasks_started", tasks=[t.get_name() for t in tasks])
    await _await_shutdown(tasks)
    log.info("shutdown_complete", final_stats=executor.stats())


def _make_book_listener(quoter: QuoterLoop):
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
