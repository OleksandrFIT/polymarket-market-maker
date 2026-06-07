"""HTTP endpoints for live dashboard.

Routes:
    GET /                — embedded HTML dashboard (auto-refresh 2s)
    GET /api/metrics     — overall stats
    GET /api/positions   — current open positions (enriched with asset/tf)
    GET /api/markets     — active markets with mid/spread/our quote count
    GET /api/fills       — recent fills (filterable: asset, tf, since, market_id)
    GET /api/resolved    — resolved bets (filterable: asset, tf, winner)
    GET /api/market_summary?id=… — full breakdown for one market
    GET /api/stats       — aggregated stats by asset/tf
    POST /api/clear      — wipe all data
    POST /api/risk       — adjust risk cap percentage

Served on ``http://0.0.0.0:8080`` by default. Local-only by convention.
"""

from __future__ import annotations

import time
from typing import Any

from aiohttp import web

from quoter.book.book_manager import BookManager
from quoter.config import Config
from quoter.ops.dashboard import HTML_DASHBOARD
from quoter.ops.live_settings import LiveSettings
from quoter.ops.logger import get_logger
from quoter.persistence.state import State
from quoter.quoter_loop import QuoterLoop
from quoter.risk.caps import RiskGuard
from quoter.strategy.inventory import Inventory

log = get_logger("metrics")


def make_app(  # noqa: C901
    *,
    cfg: Config,
    inventory: Inventory,
    executor: Any,
    quoter: QuoterLoop,
    risk: RiskGuard,
    book_manager: BookManager,
    state: State | None,
    session_ts: float = 0.0,
    live_settings: LiveSettings,
) -> web.Application:
    app = web.Application()

    def _hhmm_range(open_ts: int, expire_ts: int) -> str:
        from datetime import datetime
        a = datetime.fromtimestamp(open_ts).strftime("%H:%M")
        b = datetime.fromtimestamp(expire_ts).strftime("%H:%M")
        return f"{a}–{b}"

    def _market_status(market_id: str, expire_ts: int) -> str:
        """Status for live markets: TRADING, EXPIRED, RESOLVED."""
        if market_id in quoter.markets:
            return "TRADING" if time.time() < expire_ts else "EXPIRED"
        return "GONE"  # was tracked, no longer

    async def _db_status(market_id: str) -> str | None:
        if state is None:
            return None
        async with state.db.execute(
            "SELECT status, winning_side FROM markets WHERE market_id=?",
            (market_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        if row[0] == "RESOLVED":
            return f"RESOLVED:{row[1]}"
        return row[0]

    # ── helper: build a lookup of {market_id → (asset, timeframe)} ──
    async def _market_lookup() -> dict[str, tuple[str, str]]:
        """Get asset/timeframe for any market_id we might encounter (live + DB)."""
        # In-memory first (current/tracked markets)
        out = {m.market_id: (m.asset, m.timeframe) for m in quoter.markets.values()}
        # Augment with historical markets from DB
        if state is not None:
            async with state.db.execute("SELECT market_id, asset, timeframe FROM markets") as cur:
                async for row in cur:
                    out.setdefault(row[0], (row[1], row[2]))
        return out

    async def root(_request: web.Request) -> web.Response:
        return web.Response(text=HTML_DASHBOARD, content_type="text/html")

    def _risk_pct() -> float:
        return (risk.max_daily_loss_usd / cfg.bankroll_usd * 100.0) if cfg.bankroll_usd else 0.0

    async def metrics(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "mode": cfg.mode,
                "bankroll": cfg.bankroll_usd,
                "inventory": inventory.snapshot(),
                "executor": executor.stats(),
                "quoter": quoter.stats(),
                "risk": {
                    "stopped": risk.stopped,
                    "reason": risk.reason,
                    "max_daily_loss_usd": round(risk.max_daily_loss_usd, 2),
                    "pct": round(_risk_pct(), 1),
                },
            }
        )

    async def positions(_request: web.Request) -> web.Response:
        rows = []
        # Build {market_id → (asset, tf, open_ts, expire_ts)} from quoter + DB
        meta: dict[str, tuple[str, str, int, int]] = {}
        for m in quoter.markets.values():
            meta[m.market_id] = (m.asset, m.timeframe, m.open_ts, m.expire_ts)
        if state is not None:
            async with state.db.execute(
                "SELECT market_id, asset, timeframe, open_ts, expire_ts FROM markets"
            ) as cur:
                async for row in cur:
                    meta.setdefault(row[0], (row[1], row[2], row[3], row[4]))

        # Snapshot: the loop body awaits (DB I/O), which yields control and lets
        # a concurrent fill/resolve mutate positions → "dict changed size". Copy first.
        for mid, p in list(inventory.positions.items()):
            asset, tf, open_ts, expire_ts = meta.get(mid, ("?", "?", 0, 0))
            db_st = await _db_status(mid) if state is not None else None
            status = db_st or _market_status(mid, expire_ts)
            live_q = (
                len(executor.for_market(mid)) if hasattr(executor, "for_market") else 0
            )
            rows.append(
                {
                    "market_id": mid,
                    "asset": asset, "timeframe": tf,
                    "window": _hhmm_range(open_ts, expire_ts) if open_ts else "—",
                    "open_ts": open_ts, "expire_ts": expire_ts,
                    "status": status,
                    "live_quotes": live_q,
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
        for m in quoter.markets.values():
            yes_top = book_manager.top(m.yes_token)
            our_quotes = len(executor.for_market(m.market_id)) if hasattr(
                executor, "for_market"
            ) else 0
            status = _market_status(m.market_id, m.expire_ts)
            rows.append(
                {
                    "market_id": m.market_id,
                    "asset": m.asset, "timeframe": m.timeframe,
                    "window": _hhmm_range(m.open_ts, m.expire_ts),
                    "open_ts": m.open_ts, "expire_ts": m.expire_ts,
                    "status": status,
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
        limit = int(request.query.get("limit", "100"))
        asset = request.query.get("asset")     # "BTC" | "ETH" | None
        tf = request.query.get("tf")           # "5m" | "15m" | None
        since = request.query.get("since")     # unix ts | None
        market_id = request.query.get("market_id")
        side = request.query.get("side")       # "YES" | "NO" | None

        # JOIN fills with markets so we can filter by asset/tf
        clauses, params = [], []
        if asset:
            clauses.append("m.asset = ?")
            params.append(asset)
        if tf:
            clauses.append("m.timeframe = ?")
            params.append(tf)
        if since:
            clauses.append("f.ts >= ?")
            params.append(float(since))
        if market_id:
            clauses.append("f.market_id = ?")
            params.append(market_id)
        if side:
            clauses.append("f.side = ?")
            params.append(side)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT f.ts, f.market_id, m.asset, m.timeframe, f.side, "
            "       f.price, f.qty, f.cost "
            "FROM fills f LEFT JOIN markets m ON m.market_id = f.market_id"
            + where +
            " ORDER BY f.ts DESC LIMIT ?"
        )
        params.append(limit)
        async with state.db.execute(sql, params) as cur:
            rows = await cur.fetchall()

        # total count (subject to same filter)
        count_sql = (
            "SELECT COUNT(*) FROM fills f LEFT JOIN markets m ON m.market_id = f.market_id"
            + where
        )
        async with state.db.execute(count_sql, params[:-1]) as cur:
            total = (await cur.fetchone())[0]

        return web.json_response(
            {
                "total": total,
                "fills": [
                    {
                        "ts": r[0], "market_id": r[1],
                        "asset": r[2] or "?", "timeframe": r[3] or "?",
                        "side": r[4], "price": r[5], "qty": r[6], "cost": r[7],
                    }
                    for r in rows
                ],
            }
        )

    async def resolved(request: web.Request) -> web.Response:
        if state is None:
            return web.json_response({"resolved": []})
        asset = request.query.get("asset")
        tf = request.query.get("tf")
        winner = request.query.get("winner")   # "YES" | "NO" | None
        result = request.query.get("result")   # "win" | "loss" | "be" | None
        clauses = ["status = 'RESOLVED'"]
        params: list[Any] = []
        if asset:
            clauses.append("asset = ?")
            params.append(asset)
        if tf:
            clauses.append("timeframe = ?")
            params.append(tf)
        if winner:
            clauses.append("winning_side = ?")
            params.append(winner)
        if result == "win":
            clauses.append("resolved_pnl > 0.5")
        elif result == "loss":
            clauses.append("resolved_pnl < -0.5")
        elif result == "be":
            clauses.append("ABS(resolved_pnl) <= 0.5")
        sql = (
            "SELECT market_id, asset, timeframe, winning_side, resolved_pnl, resolved_at "
            "FROM markets WHERE " + " AND ".join(clauses) +
            " ORDER BY resolved_at DESC, rowid DESC"
        )
        async with state.db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        # Augment with cost basis from fills
        result_rows = []
        for r in rows:
            mid = r[0]
            async with state.db.execute(
                "SELECT side, SUM(qty), SUM(cost) FROM fills WHERE market_id=? GROUP BY side",
                (mid,),
            ) as cur2:
                f_rows = await cur2.fetchall()
            f = {row[0]: (row[1] or 0, row[2] or 0) for row in f_rows}
            yes_qty, yes_cost = f.get('YES', (0, 0))
            no_qty, no_cost = f.get('NO', (0, 0))
            result_rows.append({
                "market_id": mid,
                "asset": r[1], "timeframe": r[2],
                "winning_side": r[3],
                "realized_pnl": round(r[4], 2) if r[4] is not None else None,
                "resolved_at": r[5],
                "yes_qty": int(yes_qty), "no_qty": int(no_qty),
                "yes_cost": round(yes_cost, 2),
                "no_cost": round(no_cost, 2),
                "total_cost": round(yes_cost + no_cost, 2),
                "roi_pct": round(100 * r[4] / (yes_cost + no_cost), 2)
                if r[4] is not None and (yes_cost + no_cost) > 0 else None,
            })
        return web.json_response({"resolved": result_rows})

    async def market_summary(request: web.Request) -> web.Response:
        """Detailed stats for one market: fills + position + resolution."""
        if state is None:
            return web.json_response({"error": "no state"}, status=503)
        mid = request.query.get("id")
        if not mid:
            return web.json_response({"error": "id required"}, status=400)
        # Market meta
        async with state.db.execute(
            "SELECT asset, timeframe, open_ts, expire_ts, status, "
            "       winning_side, resolved_pnl, resolved_at "
            "FROM markets WHERE market_id=?", (mid,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return web.json_response({"error": "market not found"}, status=404)
        meta = {
            "market_id": mid, "asset": row[0], "timeframe": row[1],
            "open_ts": row[2], "expire_ts": row[3],
            "status": row[4], "winning_side": row[5],
            "resolved_pnl": round(row[6], 2) if row[6] is not None else None,
            "resolved_at": row[7],
        }
        # Fills (chronological)
        async with state.db.execute(
            "SELECT ts, side, price, qty, cost FROM fills "
            "WHERE market_id=? ORDER BY ts", (mid,),
        ) as cur:
            f_rows = await cur.fetchall()
        all_fills = [
            {"ts": r[0], "side": r[1], "price": r[2], "qty": r[3], "cost": r[4]}
            for r in f_rows
        ]
        # Aggregate per-side
        yes = [f for f in all_fills if f["side"] == "YES"]
        no = [f for f in all_fills if f["side"] == "NO"]
        yes_qty = sum(f["qty"] for f in yes)
        no_qty = sum(f["qty"] for f in no)
        yes_cost = sum(f["cost"] for f in yes)
        no_cost = sum(f["cost"] for f in no)
        return web.json_response(
            {
                **meta,
                "yes_qty": yes_qty, "no_qty": no_qty,
                "yes_cost": round(yes_cost, 2), "no_cost": round(no_cost, 2),
                "yes_avg": round(yes_cost / yes_qty, 4) if yes_qty else 0,
                "no_avg": round(no_cost / no_qty, 4) if no_qty else 0,
                "matched": min(yes_qty, no_qty),
                "net": yes_qty - no_qty,
                "total_cost": round(yes_cost + no_cost, 2),
                "fills": all_fills,
            }
        )

    async def stats(_request: web.Request) -> web.Response:
        """Aggregated stats by asset×timeframe across all resolved markets."""
        if state is None:
            return web.json_response({"by_kind": [], "totals": {}})
        # Sum cost across ALL markets in group via JOIN; per-row subquery
        # collapses to one market under GROUP BY (sqlite quirk).
        async with state.db.execute(
            "SELECT m.asset, m.timeframe, "
            "       COUNT(DISTINCT m.market_id) as n, "
            "       SUM(CASE WHEN m.resolved_pnl > 0.5 THEN 1 ELSE 0 END) as wins, "
            "       SUM(CASE WHEN m.resolved_pnl < -0.5 THEN 1 ELSE 0 END) as losses, "
            "       SUM(m.resolved_pnl) as pnl_sum, "
            "       COALESCE(SUM(fc.cost_sum), 0) as cost_sum "
            "FROM markets m "
            "LEFT JOIN (SELECT market_id, SUM(cost) as cost_sum FROM fills GROUP BY market_id) fc "
            "       ON fc.market_id = m.market_id "
            "WHERE m.status='RESOLVED' "
            "GROUP BY m.asset, m.timeframe"
        ) as cur:
            rows = await cur.fetchall()
        by_kind = []
        tot_n = tot_w = tot_l = 0
        tot_pnl = tot_cost = 0.0
        for r in rows:
            asset, tf, n, w, ll, pnl_sum, cost = r
            cost = cost or 0
            pnl_sum = pnl_sum or 0
            by_kind.append({
                "asset": asset, "timeframe": tf,
                "n": n, "wins": w or 0, "losses": ll or 0,
                "win_rate": round(100 * (w or 0) / ((w or 0) + (ll or 0)), 1)
                if ((w or 0) + (ll or 0)) > 0 else None,
                "pnl": round(pnl_sum, 2),
                "cost": round(cost, 2),
                "roi_pct": round(100 * pnl_sum / cost, 2) if cost > 0 else None,
            })
            tot_n += n
            tot_w += w or 0
            tot_l += ll or 0
            tot_pnl += pnl_sum
            tot_cost += cost
        totals = {
            "n": tot_n, "wins": tot_w, "losses": tot_l,
            "win_rate": round(100 * tot_w / (tot_w + tot_l), 1) if (tot_w + tot_l) > 0 else None,
            "pnl": round(tot_pnl, 2),
            "cost": round(tot_cost, 2),
            "roi_pct": round(100 * tot_pnl / tot_cost, 2) if tot_cost > 0 else None,
        }
        return web.json_response({"by_kind": by_kind, "totals": totals})

    async def stats_periods(_request: web.Request) -> web.Response:
        """Aggregated stats across 4 time buckets.

        Buckets:
            all_time         — every resolved market ever
            today            — resolved >= local midnight today
            last_session     — most recent COMPLETED session
            current_session  — markets resolved since current session start
        """
        if state is None:
            empty = {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                     "cost": 0.0, "win_rate": None, "roi_pct": None}
            return web.json_response({
                "all_time": empty, "today": empty,
                "last_session": empty, "current_session": empty,
            })

        from datetime import datetime
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

        # Last session: latest completed (ended_at IS NOT NULL, started_at != session_ts)
        async with state.db.execute(
            "SELECT started_at, ended_at FROM sessions "
            "WHERE ended_at IS NOT NULL AND started_at != ? "
            "ORDER BY started_at DESC LIMIT 1",
            (session_ts,),
        ) as cur:
            last_sess = await cur.fetchone()
        last_sess_start = last_sess[0] if last_sess else None
        last_sess_end = last_sess[1] if last_sess else None

        async def _bucket(extra_where: str, params: list[Any]) -> dict[str, Any]:
            sql = (
                "SELECT COUNT(DISTINCT m.market_id) as n, "
                "       SUM(CASE WHEN m.resolved_pnl > 0.5 THEN 1 ELSE 0 END) as wins, "
                "       SUM(CASE WHEN m.resolved_pnl < -0.5 THEN 1 ELSE 0 END) as losses, "
                "       COALESCE(SUM(m.resolved_pnl), 0) as pnl_sum, "
                "       COALESCE(SUM(fc.cost_sum), 0) as cost_sum "
                "FROM markets m "
                "LEFT JOIN (SELECT market_id, SUM(cost) as cost_sum FROM fills GROUP BY market_id) fc "
                "  ON fc.market_id = m.market_id "
                "WHERE m.status='RESOLVED' " + extra_where
            )
            async with state.db.execute(sql, params) as cur:
                row = await cur.fetchone()
            n = row[0] or 0
            wins = row[1] or 0
            losses = row[2] or 0
            pnl = row[3] or 0.0
            cost = row[4] or 0.0
            decisive = wins + losses
            return {
                "n": n, "wins": wins, "losses": losses,
                "pnl": round(pnl, 2),
                "cost": round(cost, 2),
                "win_rate": round(100 * wins / decisive, 1) if decisive > 0 else None,
                "roi_pct": round(100 * pnl / cost, 2) if cost > 0 else None,
            }

        all_time = await _bucket("", [])
        today = await _bucket("AND m.resolved_at >= ?", [today_start])
        current = await _bucket(
            "AND m.resolved_at >= ?", [session_ts]
        ) if session_ts > 0 else {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                                  "cost": 0.0, "win_rate": None, "roi_pct": None}
        if last_sess_start is not None:
            last_session = await _bucket(
                "AND m.resolved_at >= ? AND m.resolved_at <= ?",
                [last_sess_start, last_sess_end],
            )
        else:
            last_session = {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                            "cost": 0.0, "win_rate": None, "roi_pct": None}

        return web.json_response({
            "all_time": all_time,
            "today": today,
            "last_session": last_session,
            "current_session": current,
            "session_started_at": session_ts,
            "last_session_started_at": last_sess_start,
            "last_session_ended_at": last_sess_end,
        })

    async def clear(_request: web.Request) -> web.Response:
        if state is not None:
            await state.clear_all()
        inventory.reset()
        if hasattr(executor, "reset"):
            executor.reset()
        log.warning("clear_all_invoked")
        return web.json_response({"ok": True})

    async def set_risk(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            pct = float(body.get("pct"))
        except Exception:
            return web.json_response({"error": "expected JSON {pct: number}"}, status=400)
        usd = cfg.bankroll_usd * pct / 100.0
        risk.set_max_daily_loss(usd)
        return web.json_response(
            {"max_daily_loss_usd": round(usd, 2), "pct": round(pct, 1), "stopped": risk.stopped}
        )

    async def get_settings(_request: web.Request) -> web.Response:
        return web.json_response(live_settings.effective())

    async def post_settings(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            key = body["key"]
            value = body["value"]
        except Exception:
            return web.json_response({"error": "expected JSON {key, value}"}, status=400)
        try:
            result = live_settings.update(key, value)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response(result)

    app.router.add_get("/", root)
    app.router.add_get("/api/metrics", metrics)
    app.router.add_get("/api/positions", positions)
    app.router.add_get("/api/markets", markets_endpoint)
    app.router.add_get("/api/fills", fills)
    app.router.add_get("/api/resolved", resolved)
    app.router.add_get("/api/market_summary", market_summary)
    app.router.add_get("/api/stats", stats)
    app.router.add_get("/api/stats_periods", stats_periods)
    app.router.add_post("/api/clear", clear)
    app.router.add_post("/api/risk", set_risk)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_post("/api/settings", post_settings)
    return app


async def serve_forever(app: web.Application, host: str = "127.0.0.1", port: int = 8080) -> None:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    log.info("dashboard_listening", url=f"http://{host}:{port}/")
    try:
        while True:
            import asyncio
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
