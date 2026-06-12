"""Continuous merge-maker runner — reuses the proven two-sided posting + polling
logic (verified live: caught a hedged pair from ca-central-1).

The loop reads ``TradingState`` to decide whether to enter each window. Graceful
STOP lets the current window finish; FORCE STOP cancels all resting orders now.
Live, BTC-only, hard caps. Default STOPPED — never auto-trades.
"""

from __future__ import annotations

import asyncio
from time import monotonic

import httpx

from quoter.config import Config
from quoter.creds import PolyCreds
from quoter.markets import discover_markets
from quoter.strategy.ladder import compute_ladder
from quoter.execution.clob_client import ClobOps
from quoter.ops.logger import get_logger
from quoter.runner.trading_state import TradingState
from quoter.runner.requote_planner import RestingOrder, plan_requote
from quoter.runner.local_inventory import LocalInventory
from quoter.runner.ladder_planner import plan_ladder
from quoter.runner.flatten_planner import plan_flatten
from quoter.runner.trend_detector import detect_bias, sigma_remaining
from quoter.feeds.binance_ws import BinanceWS

log = get_logger("merge_runner")

FRESH_MIN_SEC = 230
BALANCED = (0.35, 0.65)
END_BUFFER_SEC = 12   # stop polling / cancel unfilled this many sec before expiry
POLL_SEC = 8
LOOP_SEC = 6
REQUOTE_SEC = 2       # re-quote cadence (read book + adjust orders this often)


def _best(book: dict, side: str) -> float | None:
    ps = [float(x["price"]) for x in (book.get(side) or [])]
    return (max(ps) if side == "bids" else min(ps)) if ps else None


def _mid(by: dict, bn: dict) -> float | None:
    yb, ya = _best(by, "bids"), _best(by, "asks")
    if yb and ya:
        return (yb + ya) / 2
    nb, na = _best(bn, "bids"), _best(bn, "asks")
    if nb and na:
        return 1 - (nb + na) / 2
    return None


class MergeRunner:
    def __init__(self, creds: PolyCreds, cfg: Config, state: TradingState,
                 requote: bool = False, target_shares: int | None = None):
        self.creds = creds
        self.cfg = cfg
        self.state = state
        self.requote = requote
        # per-side target for re-quoting (default = flat_size → one pair/window)
        self.target_shares = target_shares if target_shares else cfg.flat_size
        self.clob = ClobOps(creds)
        self._rc = None  # lazy read client (py_clob_client_v2)
        self._traded_windows: set[int] = set()
        self._shutdown = False
        self._btc_buf: list[tuple[float, float]] = []   # (price, monotonic_ts)
        self._binance = BinanceWS(("BTC",), self._on_btc) if cfg.trend_enabled else None
        self._binance_task = None

    async def _on_btc(self, asset: str, price: float, ts: float) -> None:
        """BinanceWS callback: append to the rolling buffer (arrival-time stamped) and
        drop entries older than trend_buffer_sec."""
        now = monotonic()
        self._btc_buf.append((price, now))
        cutoff = now - self.cfg.trend_buffer_sec
        self._btc_buf = [(p, t) for (p, t) in self._btc_buf if t >= cutoff]

    def _trend_bias(self, strike: float | None, time_left: float) -> str:
        """Current trend bias, or NEUTRAL when disabled / no strike / buffer stale."""
        if not self.cfg.trend_enabled or strike is None or not self._btc_buf:
            return "NEUTRAL"
        price_now, last_ts = self._btc_buf[-1]
        if monotonic() - last_ts > self.cfg.trend_stale_sec:
            return "NEUTRAL"
        sig = sigma_remaining(self._btc_buf, time_left, self.cfg)
        return detect_bias(price_now, strike, sig, time_left, self.cfg)

    def _read_client(self):
        if self._rc is None:
            from py_clob_client_v2 import ClobClient, ApiCreds
            self._rc = ClobClient(
                "https://clob.polymarket.com", 137, key=self.creds.private_key,
                creds=ApiCreds(api_key=self.creds.api_key, api_secret=self.creds.api_secret,
                               api_passphrase=self.creds.api_passphrase),
                signature_type=self.creds.sig_type, funder=self.creds.funder or None)
        return self._rc

    def collateral_usd(self) -> float:
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
        rc = self._read_client()
        try:
            b = rc.get_balance_allowance(BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL, signature_type=self.creds.sig_type))
            return int(b.get("balance", 0)) / 1_000_000
        except Exception:
            return -1.0

    def open_orders_count(self) -> int:
        try:
            return len(self._read_client().get_open_orders() or [])
        except Exception:
            return -1

    def _shares(self, token: str) -> int:
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
        rc = self._read_client()
        try:
            b = rc.get_balance_allowance(BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL, token_id=token, signature_type=self.creds.sig_type))
            return int(b.get("balance", 0)) // 1_000_000
        except Exception:
            return 0

    def _open_order_ids(self) -> set[str]:
        try:
            orders = self._read_client().get_open_orders() or []
        except Exception:
            return set()
        ids = set()
        for o in orders:
            oid = o.get("id") or o.get("orderID") or o.get("order_id")
            if oid:
                ids.add(oid)
        return ids

    async def cancel_all(self) -> int:
        try:
            n = await self.clob.cancel_all()
            log.info("runner_cancel_all", n=n)
            return n
        except Exception as e:
            log.warning("runner_cancel_all_err", error=str(e))
            return 0

    async def _current_window(self):
        mk = await discover_markets(Config(assets=("BTC",), timeframes=("5m",)),
                                    min_time_remaining_sec=5)
        if not mk:
            return None, None
        m = mk[0]
        async with httpx.AsyncClient(timeout=8) as cl:
            by = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.yes_token})).json()
            bn = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.no_token})).json()
        return m, _mid(by, bn)

    async def trade_window(self, m, mid: float) -> None:
        """Post both legs for this window; HOLD matched pairs to on-chain
        resolution (redeem manually for now — no merge step); cancel unfilled at
        the end. Breaks early (cancelling its own resting orders) on FORCE STOP."""
        tte = m.time_remaining()
        quotes = compute_ladder(self.cfg, mid, tte,
                                inventory_yes_qty=0, inventory_no_qty=0,
                                inventory_yes_cost=0.0, inventory_no_cost=0.0)
        if not quotes:
            return
        # HARD CAP enforced HERE. compute_ladder sees zero inventory by design (we
        # post once per window and never re-quote), so its capital/balance gates
        # cannot fire — the runner is the cap authority. Skip the window if the
        # single post's intended spend would exceed per_market_cap_usd.
        intended = sum(q.price * q.size for q in quotes)
        if intended > self.cfg.per_market_cap_usd + 1e-9:
            log.warning("runner_over_cap_skip", slug=m.slug,
                        intended=round(intended, 2), cap=self.cfg.per_market_cap_usd)
            return
        self._traded_windows.add(m.open_ts)
        self.state.windows_traded += 1
        self.state.last_window = m.slug
        self.state.last_event = f"entered {m.slug} (mid {mid:.2f})"
        log.info("runner_enter_window", slug=m.slug, mid=round(mid, 3))

        oids: list[str] = []
        for q in quotes:
            tok = m.yes_token if q.side == "YES" else m.no_token
            r = await self.clob.place_limit(token_id=tok, price=q.price, size=q.size,
                                            side="BUY", post_only=True)
            if r and r.get("order_id"):
                oids.append(r["order_id"])
        log.info("runner_posted", slug=m.slug, legs=len(oids))

        # Poll until window end or FORCE STOP. The flag is checked every 1s for a
        # responsive emergency stop; shares are re-read every POLL_SEC. We do NOT
        # drain force_stop here — run_forever drains it and runs cancel_all as a
        # backstop (defense-in-depth); draining here would break that.
        secs = 0
        while m.time_remaining() > END_BUFFER_SEC:
            if self.state.force_stop_requested:
                log.info("runner_force_break", slug=m.slug)
                break
            await asyncio.sleep(1)
            secs += 1
            if secs % POLL_SEC == 0:
                yq, nq = self._shares(m.yes_token), self._shares(m.no_token)
                self.state.pairs_caught = min(yq, nq)
                self.state.naked_shares = abs(yq - nq)

        # Cancel any unfilled resting legs (graceful end or force break).
        if oids:
            await self.clob.cancel_orders(oids)
        yq, nq = self._shares(m.yes_token), self._shares(m.no_token)
        self.state.last_event = f"window done: matched={min(yq, nq)} naked={abs(yq - nq)}"
        log.info("runner_window_done", slug=m.slug, matched=min(yq, nq), naked=abs(yq - nq))

    async def run_forever(self) -> None:
        log.info("runner_started", mode=self.state.mode)
        if self._binance is not None and self._binance_task is None:
            self._binance_task = asyncio.create_task(self._binance.run())
        while not self._shutdown:
            try:
                if self.state.drain_force_stop():
                    n = await self.cancel_all()
                    self.state.last_event = f"FORCE STOP — cancelled {n} resting"
                m, mid = await self._current_window()
                if m is not None:
                    enter = self.state.should_enter(
                        window_open_ts=m.open_ts, time_left=m.time_remaining(), mid=mid,
                        fresh_min_sec=FRESH_MIN_SEC, balanced=BALANCED,
                        already_traded=m.open_ts in self._traded_windows)
                    if enter:
                        if self.requote and self.cfg.rungs > 1:
                            await self._ladder_window(m, mid)
                        elif self.requote:
                            await self._requote_window(m, mid)
                        else:
                            await self.trade_window(m, mid)
            except Exception as e:
                log.warning("runner_loop_err", error=str(e))
            await asyncio.sleep(LOOP_SEC)

    async def _requote_window(self, m, mid_at_entry: float) -> None:
        """LIVE continuous re-quoting: keep top-of-book on the side(s) we need,
        using the tested ``plan_requote`` brain. Spend is tracked via the
        collateral delta; matched pairs ride to resolution. Same hard caps + STOP.

        NOTE: this is the live-execution path for re-quoting — the brain is unit/
        simulation-tested; this glue is validated operator-gated (live), never run
        without an explicit START.
        """
        self._traded_windows.add(m.open_ts)
        self.state.windows_traded += 1
        self.state.last_window = m.slug
        self.state.last_event = f"re-quoting {m.slug} (mid {mid_at_entry:.2f})"
        log.info("runner_requote_enter", slug=m.slug, mid=round(mid_at_entry, 3))

        coll_start = self.collateral_usd()
        resting: dict[str, RestingOrder | None] = {"YES": None, "NO": None}
        placed_at: dict[str, float] = {"YES": 0.0, "NO": 0.0}
        last_px: dict[str, float] = {"YES": 0.0, "NO": 0.0}
        # Optimistic local inventory: credit a fill the MOMENT our order vanishes
        # uncancelled, because the on-chain share read lags fills by seconds while
        # this loop runs every ~2s. Trusting the lagging read re-posted sides we
        # had already filled and over-bought one leg (15 vs target 5, naked 9 vs
        # cap 5). The chain read only ever RAISES the local count (partials),
        # never lowers it. Also carries the cost basis for the edge gate.
        local = LocalInventory()

        try:
            while m.time_remaining() > END_BUFFER_SEC:
                if self.state.force_stop_requested:
                    log.info("runner_requote_force_break", slug=m.slug)
                    break
                try:
                    async with httpx.AsyncClient(timeout=6) as cl:
                        by = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.yes_token})).json()
                        bn = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.no_token})).json()
                except Exception:
                    await asyncio.sleep(REQUOTE_SEC)
                    continue
                yes_bid, no_bid = _best(by, "bids"), _best(bn, "bids")
                yes_ask, no_ask = _best(by, "asks"), _best(bn, "asks")
                if not yes_bid or not no_bid:
                    await asyncio.sleep(REQUOTE_SEC)
                    continue
                chain_yes, chain_no = self._shares(m.yes_token), self._shares(m.no_token)
                coll_now = self.collateral_usd()
                now = monotonic()

                # Reconcile our tracked orders. An order that vanished from the
                # book WITHOUT us cancelling it has FILLED — credit it to local
                # inventory immediately, don't wait for the lagging chain read.
                # Grace of one cadence guards a just-placed order that's missing
                # from a stale get_open_orders read (avoids double-posting).
                open_ids = self._open_order_ids()
                for side in ("YES", "NO"):
                    ro = resting[side]
                    if ro is not None and ro.order_id not in open_ids and (now - placed_at[side]) > REQUOTE_SEC:
                        local.credit_fill(side, ro.size, ro.price)
                        resting[side] = None
                # Backstop: raise local inventory to the chain read when it's
                # higher (partial fills, or a fill we missed crediting); never
                # lower it — a low read is lag, and under-counting caused the over-buy.
                local.reconcile_up("YES", chain_yes, last_px["YES"] or yes_bid)
                local.reconcile_up("NO", chain_no, last_px["NO"] or no_bid)
                inv_yes, inv_no = local.inv["YES"], local.inv["NO"]

                # FORWARD-LOOKING committed spend: realized (balance-delta OR
                # local cost-basis, whichever larger — fail-safe) PLUS resting value.
                spent_bal = (coll_start - coll_now) if (coll_start >= 0 and coll_now >= 0) else 0.0
                realized = max(spent_bal, local.cost["YES"] + local.cost["NO"])
                resting_val = sum(ro.price * ro.size for ro in resting.values() if ro is not None)
                committed = max(0.0, realized) + resting_val

                plan = plan_requote(
                    yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
                    inv_yes=inv_yes, inv_no=inv_no,
                    yes_cost=local.cost["YES"], no_cost=local.cost["NO"],
                    committed=committed, target_shares=self.target_shares,
                    resting=resting, cfg=self.cfg)

                # batch cancels (fewer requests)
                if plan.cancels:
                    await self.clob.cancel_orders(plan.cancels)
                    for s in ("YES", "NO"):
                        if resting[s] is not None and resting[s].order_id in plan.cancels:
                            resting[s] = None

                # only add exposure that keeps committed within the cap
                post_cost = sum(q.price * q.size for q in plan.posts)
                if plan.posts and committed + post_cost <= self.cfg.per_market_cap_usd + 1e-9:
                    for q in plan.posts:
                        tok = m.yes_token if q.side == "YES" else m.no_token
                        r = await self.clob.place_limit(token_id=tok, price=q.price, size=q.size,
                                                        side="BUY", post_only=True)
                        if r and r.get("order_id"):
                            resting[q.side] = RestingOrder(r["order_id"], q.side, q.price, q.size)
                            placed_at[q.side] = now
                            last_px[q.side] = q.price

                self.state.pairs_caught = min(inv_yes, inv_no)
                self.state.naked_shares = abs(inv_yes - inv_no)
                await asyncio.sleep(REQUOTE_SEC)
        finally:
            # Robust cleanup on graceful end, FORCE STOP, or any exception:
            # cancel ALL our resting orders account-wide (covers any order dropped
            # from local tracking). Held positions/pairs are untouched.
            await self.cancel_all()

        iy = max(self._shares(m.yes_token), local.inv["YES"])
        inn = max(self._shares(m.no_token), local.inv["NO"])
        self.state.last_event = f"re-quote done: matched={min(iy, inn)} naked={abs(iy - inn)}"
        log.info("runner_requote_done", slug=m.slug, matched=min(iy, inn), naked=abs(iy - inn))

    async def _ladder_window(self, m, mid_at_entry: float) -> None:
        """LIVE laddered re-quoting: rest a deep ladder of bids both sides (anchor per
        cfg.ladder_anchor), credit fills to LocalInventory, cap naked by pulling the
        heavier side's rungs. Operator-gated; the brain (plan_ladder) is sim-tested."""
        self._traded_windows.add(m.open_ts)
        self.state.windows_traded += 1
        self.state.last_window = m.slug
        self.state.last_event = f"laddering {m.slug} (mid {mid_at_entry:.2f})"
        log.info("runner_ladder_enter", slug=m.slug, mid=round(mid_at_entry, 3))

        coll_start = self.collateral_usd()
        entry_mid = mid_at_entry
        resting: dict[str, list[RestingOrder]] = {"YES": [], "NO": []}
        placed_at: dict[str, float] = {}     # order_id -> monotonic time placed
        last_px: dict[str, float] = {"YES": 0.0, "NO": 0.0}
        local = LocalInventory()
        flattened: set[str] = set()
        naked_since: dict[str, float | None] = {"YES": None, "NO": None}
        strike = self._btc_buf[-1][0] if self._btc_buf else None

        try:
            while m.time_remaining() > END_BUFFER_SEC:
                if self.state.force_stop_requested:
                    log.info("runner_ladder_force_break", slug=m.slug)
                    break
                try:
                    async with httpx.AsyncClient(timeout=6) as cl:
                        by = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.yes_token})).json()
                        bn = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.no_token})).json()
                except Exception:
                    await asyncio.sleep(REQUOTE_SEC)
                    continue
                yes_bid, no_bid = _best(by, "bids"), _best(bn, "bids")
                yes_ask, no_ask = _best(by, "asks"), _best(bn, "asks")
                if not yes_bid or not no_bid:
                    await asyncio.sleep(REQUOTE_SEC)
                    continue

                chain_yes, chain_no = self._shares(m.yes_token), self._shares(m.no_token)
                coll_now = self.collateral_usd()
                now = monotonic()

                # Credit filled rungs (vanished uncancelled), drop them from the list.
                open_ids = self._open_order_ids()
                for side in ("YES", "NO"):
                    kept = []
                    for ro in resting[side]:
                        if ro.order_id not in open_ids and (now - placed_at.get(ro.order_id, 0.0)) > REQUOTE_SEC:
                            local.credit_fill(side, ro.size, ro.price)
                            placed_at.pop(ro.order_id, None)
                        else:
                            kept.append(ro)
                    resting[side] = kept
                # After a flatten the chain read lags HIGH (still shows the sold
                # shares) — reconcile_up only raises, so it would re-add them. Skip
                # the flattened side; our local count is authoritative there.
                if "YES" not in flattened:
                    local.reconcile_up("YES", chain_yes, last_px["YES"] or yes_bid)
                if "NO" not in flattened:
                    local.reconcile_up("NO", chain_no, last_px["NO"] or no_bid)
                inv_yes, inv_no = local.inv["YES"], local.inv["NO"]

                spent_bal = (coll_start - coll_now) if (coll_start >= 0 and coll_now >= 0) else 0.0
                realized = max(spent_bal, local.cost["YES"] + local.cost["NO"])
                resting_val = sum(ro.price * ro.size for s in ("YES", "NO") for ro in resting[s])
                committed = max(0.0, realized) + resting_val

                # Auto-flat: sell a naked leg that has PERSISTED at the cap past the
                # grace (or once the window is nearly over), then suppress that side
                # for the rest of the window. Mirrors test_ladder_sim's gate.
                if self.cfg.auto_flat:
                    naked = inv_yes - inv_no
                    heavy = "YES" if naked > 0 else ("NO" if naked < 0 else None)
                    for s in ("YES", "NO"):
                        if s != heavy:
                            naked_since[s] = None
                    if heavy and abs(naked) >= self.cfg.naked_cap and heavy not in flattened:
                        if naked_since[heavy] is None:
                            naked_since[heavy] = now
                        grace_done = (now - naked_since[heavy]) >= self.cfg.flatten_grace_sec
                        near_end = m.time_remaining() <= self.cfg.flatten_grace_sec
                        if grace_done or near_end:
                            dec = plan_flatten(inv_yes, inv_no, self.cfg.naked_cap)
                            if dec:
                                bid = yes_bid if dec.side == "YES" else no_bid
                                tok = m.yes_token if dec.side == "YES" else m.no_token
                                r = await self.clob.place_limit(
                                    token_id=tok, price=bid, size=dec.qty,
                                    side="SELL", post_only=False)
                                if r and r.get("order_id"):
                                    local.debit_fill(dec.side, dec.qty, bid)
                                    flattened.add(dec.side)
                                    inv_yes, inv_no = local.inv["YES"], local.inv["NO"]
                                    log.info("runner_ladder_flatten", slug=m.slug,
                                             side=dec.side, qty=dec.qty,
                                             price=round(bid, 3))
                    elif heavy and abs(naked) < self.cfg.naked_cap:
                        naked_since[heavy] = None

                tbias = self._trend_bias(strike, m.time_remaining())
                self.state.last_event = f"laddering {m.slug} (bias {tbias})"
                plan = plan_ladder(
                    yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
                    entry_mid=entry_mid, inv_yes=inv_yes, inv_no=inv_no,
                    yes_cost=local.cost["YES"], no_cost=local.cost["NO"],
                    committed=committed, resting=resting, cfg=self.cfg,
                    trend_bias=tbias, suppressed=frozenset(flattened))

                if plan.cancels:
                    await self.clob.cancel_orders(plan.cancels)
                    for side in ("YES", "NO"):
                        resting[side] = [ro for ro in resting[side] if ro.order_id not in plan.cancels]

                for q in plan.posts:
                    post_cost = q.price * q.size
                    if committed + post_cost > self.cfg.per_window_cap + 1e-9:
                        continue
                    tok = m.yes_token if q.side == "YES" else m.no_token
                    r = await self.clob.place_limit(token_id=tok, price=q.price, size=q.size,
                                                    side="BUY", post_only=True)
                    if r and r.get("order_id"):
                        resting[q.side].append(RestingOrder(r["order_id"], q.side, q.price, q.size))
                        placed_at[r["order_id"]] = now
                        last_px[q.side] = q.price
                        committed += post_cost

                self.state.pairs_caught = min(inv_yes, inv_no)
                self.state.naked_shares = abs(inv_yes - inv_no)
                await asyncio.sleep(REQUOTE_SEC)
        finally:
            await self.cancel_all()

        iy = max(self._shares(m.yes_token), local.inv["YES"])
        inn = max(self._shares(m.no_token), local.inv["NO"])
        self.state.last_event = f"ladder done: matched={min(iy, inn)} naked={abs(iy - inn)}"
        log.info("runner_ladder_done", slug=m.slug, matched=min(iy, inn), naked=abs(iy - inn))

    def shutdown(self) -> None:
        self._shutdown = True
