"""HTTP endpoints for live dashboard.

Routes:
    GET /                — embedded HTML dashboard (auto-refresh 2s)
    GET /api/metrics     — overall stats (mode, bankroll, inventory, executor, risk)
    GET /api/positions   — current open positions
    GET /api/markets     — active markets with mid/spread/our quote count
    GET /api/fills       — recent fills from SQLite (?limit=N)

Served on ``http://0.0.0.0:8080`` by default. Local-only by convention.
"""

from __future__ import annotations

import time
from typing import Any

from aiohttp import web

from quoter.book.book_manager import BookManager
from quoter.config import Config
from quoter.markets import Market
from quoter.ops.dashboard import HTML_DASHBOARD
from quoter.ops.logger import get_logger
from quoter.persistence.state import State
from quoter.quoter_loop import QuoterLoop
from quoter.risk.caps import RiskGuard
from quoter.strategy.inventory import Inventory

log = get_logger("metrics")


def make_app(
    *,
    cfg: Config,
    inventory: Inventory,
    executor: Any,
    quoter: QuoterLoop,
    risk: RiskGuard,
    markets: list[Market],
    book_manager: BookManager,
    state: State | None,
) -> web.Application:
    app = web.Application()

    async def root(_request: web.Request) -> web.Response:
        return web.Response(text=HTML_DASHBOARD, content_type="text/html")

    async def metrics(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "mode": cfg.mode,
                "bankroll": cfg.bankroll_usd,
                "inventory": inventory.snapshot(),
                "executor": executor.stats(),
                "quoter": quoter.stats(),
                "risk": {"stopped": risk.stopped, "reason": risk.reason},
            }
        )

    async def positions(_request: web.Request) -> web.Response:
        rows = []
        for mid, p in inventory.positions.items():
            rows.append(
                {
                    "market_id": mid,
                    "yes_qty": p.yes_qty, "no_qty": p.no_qty,
                    "yes_avg": round(p.yes_avg, 4),
                    "no_avg": round(p.no_avg, 4),
                    "matched": p.matched,
                    "net": p.net_yes_minus_no,
                    "total_cost": round(p.total_cost, 2),
                }
            )
        return web.json_response({"positions": rows})

    async def markets_endpoint(_request: web.Request) -> web.Response:
        rows = []
        for m in markets:
            yes_top = book_manager.top(m.yes_token)
            our_quotes = len(executor.for_market(m.market_id)) if hasattr(
                executor, "for_market"
            ) else 0
            rows.append(
                {
                    "market_id": m.market_id,
                    "asset": m.asset, "timeframe": m.timeframe,
                    "mid_yes": yes_top.mid if yes_top and yes_top.mid else None,
                    "spread": yes_top.spread if yes_top and yes_top.spread else None,
                    "our_quotes": our_quotes,
                    "expires_in": max(0, int(m.expire_ts - time.time())),
                }
            )
        return web.json_response({"markets": rows})

    async def fills(request: web.Request) -> web.Response:
        if state is None:
            return web.json_response({"fills": [], "total": 0})
        limit = int(request.query.get("limit", "50"))
        async with state.db.execute(
            "SELECT ts, market_id, side, price, qty, cost FROM fills "
            "ORDER BY ts DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        total = await state.n_fills()
        return web.json_response(
            {
                "total": total,
                "fills": [
                    {"ts": r[0], "market_id": r[1], "side": r[2],
                     "price": r[3], "qty": r[4], "cost": r[5]}
                    for r in rows
                ],
            }
        )

    app.router.add_get("/", root)
    app.router.add_get("/api/metrics", metrics)
    app.router.add_get("/api/positions", positions)
    app.router.add_get("/api/markets", markets_endpoint)
    app.router.add_get("/api/fills", fills)
    return app


async def serve_forever(app: web.Application, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Start the HTTP server. Cancels via task.cancel() externally."""
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    log.info("dashboard_listening", url=f"http://{host}:{port}/")
    try:
        # Block until cancelled
        while True:
            import asyncio
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
