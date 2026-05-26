"""poly-quoter entrypoint.

Phase 1 scope: connect to Binance + Polymarket market WS, log events.
Quoter loop, inventory, execution — added in later phases.

Run with: ``uv run python -m quoter.main``
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Any

import uvloop
from dotenv import load_dotenv

# Load .env from project root before importing other modules that may read env
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

uvloop.install()

from quoter.config import Config  # noqa: E402
from quoter.feeds.binance_ws import BinanceWS  # noqa: E402
from quoter.feeds.poly_market_ws import PolyMarketWS  # noqa: E402
from quoter.markets import Market, discover_markets  # noqa: E402
from quoter.ops.logger import get_logger, setup_logging  # noqa: E402


def _make_btc_logger(log: Any) -> Any:
    """Build a throttled callback that logs every 100th Binance tick per asset."""
    counts: dict[str, int] = {}

    async def on_price(asset: str, price: float, ts: float) -> None:
        counts[asset] = counts.get(asset, 0) + 1
        if counts[asset] % 100 == 1:
            log.info("binance_tick_sampled", asset=asset, price=price, ts=ts)

    return on_price


def _make_poly_logger(log: Any, by_token: dict[str, Market]) -> Any:
    """Build a callback that summarizes Polymarket WS events to log."""

    async def on_event(msg: dict) -> None:
        event_type = msg.get("event_type", "?")
        asset_id = msg.get("asset_id") or msg.get("market") or "?"
        m = by_token.get(asset_id)
        market_id = m.market_id[:12] if m else "unknown"
        if event_type == "book":
            bids = msg.get("bids", [])
            asks = msg.get("asks", [])
            log.info(
                "poly_book",
                market=market_id,
                asset=m.asset if m else "?",
                side="YES" if m and m.yes_token == asset_id else "NO",
                bid=bids[-1]["price"] if bids else None,
                ask=asks[0]["price"] if asks else None,
                n_bids=len(bids),
                n_asks=len(asks),
            )
        elif event_type == "price_change":
            log.debug("poly_price_change", market=market_id, changes=len(msg.get("price_changes", [])))
        elif event_type == "last_trade_price":
            log.debug("poly_trade", market=market_id, px=msg.get("price"), sz=msg.get("size"))

    return on_event


async def _await_shutdown(tasks: list[asyncio.Task]) -> None:
    """Block until SIGINT/SIGTERM, then cancel all tasks."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _amain() -> None:
    cfg = Config.from_env()
    setup_logging(cfg.log_path, cfg.log_level)
    log = get_logger("main")
    log.info(
        "startup",
        mode=cfg.mode,
        bankroll=cfg.bankroll_usd,
        assets=list(cfg.assets),
        timeframes=list(cfg.timeframes),
    )

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
            asset=m.asset,
            tf=m.timeframe,
            condition=m.market_id[:12],
            expires_in=int(m.time_remaining()),
        )

    binance = BinanceWS(cfg.assets, _make_btc_logger(log))
    poly = PolyMarketWS(cfg.ws_market_url, _make_poly_logger(log, by_token))
    poly.set_subscriptions(list(by_token.keys()))

    tasks = [
        asyncio.create_task(binance.run(), name="binance_ws"),
        asyncio.create_task(poly.run(), name="poly_market_ws"),
    ]
    log.info("running_tasks_started")
    await _await_shutdown(tasks)
    log.info("shutdown_complete")


def main() -> None:
    """Sync entrypoint for ``python -m quoter.main`` or installed script."""
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
