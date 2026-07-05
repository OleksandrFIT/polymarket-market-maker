"""Continuous merge-maker runner — reuses the proven two-sided posting + polling
logic (verified live: caught a hedged pair from ca-central-1).

The loop reads ``TradingState`` to decide whether to enter each window. Graceful
STOP lets the current window finish; FORCE STOP cancels all resting orders now.
Live, BTC-only, hard caps. Default STOPPED — never auto-trades.
"""

from __future__ import annotations

import asyncio
from time import monotonic
import time

import httpx

from quoter.config import Config
from quoter.creds import PolyCreds
from quoter.execution.clob_client import ClobOps
from quoter.feeds.binance_ws import BinanceWS
from quoter.markets import discover_markets
from quoter.ops.logger import get_logger
from quoter.runner.fill_inventory import inventory_from_fills
from quoter.runner.fills_feed import fetch_window_fills
from quoter.runner.flatten_planner import (
    balance_complete_qty,
    complete_cap_qty,
    naked_action_due,
    plan_naked_action,
    recent_complete_qty,
)
from quoter.runner.ladder_planner import plan_ladder
from quoter.runner.local_inventory import LocalInventory
from quoter.runner.requote_planner import RestingOrder, plan_requote
from quoter.runner.trading_state import TradingState
from quoter.runner.trend_detector import detect_bias, sigma_remaining
from quoter.runner.tilt_planner import plan_tilt
from quoter.runner.regime_tracker import RegimeTracker
from quoter.runner.five_min_planner import plan_five_min
from quoter.runner.top_book_planner import plan_top_book, diff_quotes, plan_merge, committed_gate, skew_ok
from quoter.runner.paper_fill import PaperBook
from quoter.strategy.ladder import compute_ladder

log = get_logger("merge_runner")

FRESH_MIN_SEC = 230
BALANCED = (0.35, 0.65)
END_BUFFER_SEC = 12   # stop polling / cancel unfilled this many sec before expiry
POLL_SEC = 8
LOOP_SEC = 6
REQUOTE_SEC = 2       # re-quote cadence (read book + adjust orders this often)
WRAP_MIN_USD = 5.0    # sweep USDC.e -> pUSD only above this (skip dust; 1 tx/pass max)


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
        self._regime = RegimeTracker(cfg.regime_window, cfg.regime_min_samples,
                                     cfg.regime_min_ev, cfg.tilt_fee)
        self._binance_task = None
        self._redeem_task = None

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
            from py_clob_client_v2 import ApiCreds, ClobClient
            self._rc = ClobClient(
                "https://clob.polymarket.com", 137, key=self.creds.private_key,
                creds=ApiCreds(api_key=self.creds.api_key, api_secret=self.creds.api_secret,
                               api_passphrase=self.creds.api_passphrase),
                signature_type=self.creds.sig_type, funder=self.creds.funder or None)
        return self._rc

    def collateral_usd(self) -> float:
        from py_clob_client_v2 import AssetType, BalanceAllowanceParams
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
        from py_clob_client_v2 import AssetType, BalanceAllowanceParams
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

    def _order_matched(self, order_id: str) -> float | None:
        """Matched (filled) size of an order via the read client's GET /order/{id}
        (same py_clob_client_v2 client `_open_order_ids` uses). None on any failure
        — the caller picks the conservative fallback."""
        try:
            o = self._read_client().get_order(order_id) or {}
            if "size_matched" not in o:
                return None    # schema drift -> let the caller take its conservative fallback
            return float(o.get("size_matched") or 0)
        except Exception:
            return None

    async def _place_limit(self, **kw):
        """Place a limit order, or in dry_run just log the INTENDED order and return
        a stub (no real order). All order placement MUST go through here."""
        if self.cfg.dry_run:
            log.info("dryrun_place", token_id=kw.get("token_id"), price=kw.get("price"),
                     size=kw.get("size"), side=kw.get("side"),
                     order_type=kw.get("order_type", "GTC"), post_only=kw.get("post_only"))
            return {"order_id": "dryrun", "status": "dry"}
        return await self.clob.place_limit(**kw)

    async def _cancel_orders(self, oids):
        """Cancel orders, or no-op in dry_run (we never placed any)."""
        if self.cfg.dry_run:
            return None
        return await self.clob.cancel_orders(oids)

    async def cancel_all(self) -> int:
        if self.cfg.dry_run:
            log.info("dryrun_cancel_all")
            return 0
        try:
            n = await self.clob.cancel_all()
            log.info("runner_cancel_all", n=n)
            return n
        except Exception as e:
            log.warning("runner_cancel_all_err", error=str(e))
            return 0

    def _discovery_cfg(self) -> Config:
        """Config for market discovery — assets/timeframes come from our config."""
        return Config(assets=self.cfg.assets, timeframes=self.cfg.timeframes)

    async def _current_window(self):
        mk = await discover_markets(self._discovery_cfg(), min_time_remaining_sec=5)
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
            r = await self._place_limit(token_id=tok, price=q.price, size=q.size,
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
            await self._cancel_orders(oids)
        yq, nq = self._shares(m.yes_token), self._shares(m.no_token)
        self.state.last_event = f"window done: matched={min(yq, nq)} naked={abs(yq - nq)}"
        log.info("runner_window_done", slug=m.slug, matched=min(yq, nq), naked=abs(yq - nq))

    async def run_forever(self) -> None:
        log.info("runner_started", mode=self.state.mode)
        if self._binance is not None and self._binance_task is None:
            self._binance_task = asyncio.create_task(self._binance.run())
        if self.cfg.strategy == "top_book" and self._redeem_task is None:
            self._redeem_task = asyncio.create_task(self._redeem_sweeper())
        while not self._shutdown:
            try:
                if self.state.drain_force_stop():
                    n = await self.cancel_all()
                    self.state.last_event = f"FORCE STOP — cancelled {n} resting"
                # hard total deadline: a Cloudflare event-page GET can drip its body
                # forever (per-chunk read timeout never fires) — froze the loop for 12h
                # on 2026-07-02. TimeoutError lands in runner_loop_err -> next iteration.
                m, mid = await asyncio.wait_for(self._current_window(), timeout=25)
                if m is not None:
                    enter = self.state.should_enter(
                        window_open_ts=m.open_ts, time_left=m.time_remaining(), mid=mid,
                        fresh_min_sec=FRESH_MIN_SEC, balanced=BALANCED,
                        already_traded=m.open_ts in self._traded_windows)
                    if enter:
                        if self.cfg.strategy == "top_book":
                            await self._top_book_window(m, mid)
                        elif self.cfg.strategy == "five_min":
                            await self._five_min_window(m, mid)
                        elif self.requote and self.cfg.rungs > 1:
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
                    await self._cancel_orders(plan.cancels)
                    for s in ("YES", "NO"):
                        if resting[s] is not None and resting[s].order_id in plan.cancels:
                            resting[s] = None

                # only add exposure that keeps committed within the cap
                post_cost = sum(q.price * q.size for q in plan.posts)
                if plan.posts and committed + post_cost <= self.cfg.per_market_cap_usd + 1e-9:
                    for q in plan.posts:
                        tok = m.yes_token if q.side == "YES" else m.no_token
                        r = await self._place_limit(token_id=tok, price=q.price, size=q.size,
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
        cfg.ladder_anchor), rebuild inventory from real fills, cap naked by pulling the
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
        local = LocalInventory()              # optimistic posting/cap inventory (sweep-proof)
        last_real = inventory_from_fills([])   # last good real-fills inventory (phantom-kill)
        flattened: set[str] = set()
        naked_since: dict[str, float | None] = {"YES": None, "NO": None}
        last_naked_try: dict[str, float | None] = {"YES": None, "NO": None}
        completed_taker: dict[str, float] = {"YES": 0.0, "NO": 0.0}  # cumulative COMPLETE buys/side
        # continuous mode: per-side log of (time, qty) recent completes — only those
        # inside the feed-lag window are subtracted from naked (lag-safe top-up).
        complete_log: dict[str, list[tuple[float, float]]] = {"YES": [], "NO": []}
        # Lag-proof sweep backstop: count shares we actually POST per side this
        # window (monotonic, never reset). Independent of the fill-inventory
        # count, which reconcile_down can wrongly reset under data-api lag — the
        # cause of the window-6 sweep (20 naked Up vs cap 5). Hard-caps one-sided
        # posting so naked can never exceed naked_cap + rung_size, regardless of
        # what the inventory count believes.
        posted: dict[str, float] = {"YES": 0.0, "NO": 0.0}
        # In deep-ladder MEASUREMENT mode the share backstop is intentionally relaxed —
        # the binding bound is per_window_cap (a REAL collateral-spend bound, lag-proof),
        # so we can rest+repost the full static deep ladder. Normal mode keeps the tight
        # lag-proof share cap that bounds the sweep bug.
        post_cap = (10 ** 9 if self.cfg.deep_ladder
                    else self.cfg.naked_cap + self.cfg.rung_size)
        strike = self._btc_buf[-1][0] if self._btc_buf else None
        last_bias = "NEUTRAL"          # last non-NEUTRAL detector call this window
        last_fav_entry = 0.0           # favorite ask at that call (shadow/real entry)
        last_mid = mid_at_entry        # last seen YES mid (for end-of-window winner)
        rtt_samples: list[float] = []   # book-fetch latency (2 GETs), ms — gauges queue speed

        try:
            while m.time_remaining() > END_BUFFER_SEC:
                if self.state.force_stop_requested:
                    log.info("runner_ladder_force_break", slug=m.slug)
                    break
                async with httpx.AsyncClient(timeout=6) as cl:
                    try:
                        _t_rtt = monotonic()
                        by = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.yes_token})).json()
                        bn = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.no_token})).json()
                        rtt_samples.append((monotonic() - _t_rtt) * 1000.0)
                    except Exception:
                        await asyncio.sleep(REQUOTE_SEC)
                        continue
                    yes_bid, no_bid = _best(by, "bids"), _best(bn, "bids")
                    yes_ask, no_ask = _best(by, "asks"), _best(bn, "asks")
                    if not yes_bid or not no_bid:
                        await asyncio.sleep(REQUOTE_SEC)
                        continue

                    coll_now = self.collateral_usd()
                    now = monotonic()

                    # Credit OUR vanished-uncancelled rungs immediately (optimistic, lag-proof)
                    # and drop them from resting so the ladder stays coherent.
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
                    # Reconcile the optimistic count against the REAL fills feed: raise to real
                    # (caught fills), and lower to real after the grace (kills phantom credits).
                    try:
                        fills = await fetch_window_fills(self.creds.funder, m.slug, cl)
                        last_real = inventory_from_fills(fills)
                        inv_ok = True
                    except Exception:
                        inv_ok = False
                    # Only reconcile against a FRESH real-fills read. On a feed outage
                    # `last_real` is stale (possibly low) — reconciling against it could
                    # wrongly lower the optimistic count, so skip it this tick.
                    if inv_ok:
                        for side in ("YES", "NO"):
                            local.reconcile_up(side, last_real.inv[side], last_px[side] or (yes_bid if side == "YES" else no_bid))
                            local.reconcile_down(side, last_real.inv[side], now, self.cfg.inv_reconcile_grace_sec)
                    inv = local
                    inv_yes, inv_no = inv.inv["YES"], inv.inv["NO"]

                spent_bal = (coll_start - coll_now) if (coll_start >= 0 and coll_now >= 0) else 0.0
                realized = max(spent_bal, inv.cost["YES"] + inv.cost["NO"])
                resting_val = sum(ro.price * ro.size for s in ("YES", "NO") for ro in resting[s])
                committed = max(0.0, realized) + resting_val

                # Trend bias computed once per tick: gates BOTH the completion guard
                # below (don't pair off a deliberate tilt) and the directional tilt block.
                tbias = self._trend_bias(strike, m.time_remaining())

                if (self.cfg.auto_flat or self.cfg.complete_pairs) and inv_ok:
                    naked = inv_yes - inv_no
                    heavy = "YES" if naked > 0 else ("NO" if naked < 0 else None)
                    for s in ("YES", "NO"):
                        if s != heavy:
                            naked_since[s] = None
                    if heavy and heavy not in flattened:
                        # legacy auto_flat tracks naked_since once at/over the cap
                        if (self.cfg.auto_flat and not self.cfg.complete_pairs
                                and abs(naked) >= self.cfg.naked_cap
                                and naked_since[heavy] is None):
                            naked_since[heavy] = now
                        due = naked_action_due(self.cfg, naked, m.time_remaining(),
                                               naked_since[heavy], now)
                        # near_end widens the SELL gate to flatten_grace if larger;
                        # in complete_pairs mode due already implies near_end.
                        near_end = m.time_remaining() <= max(self.cfg.flatten_grace_sec,
                                                             self.cfg.complete_gate_sec)
                        # cooldown ALWAYS applies (even near_end): the gate is long
                        # vs the 6s cooldown, so a SELL still fires — but COMPLETE no
                        # longer re-buys a crashing light leg every tick (the bug that
                        # bought 20 Down vs 10 Up in live window 1781627400, -$2.10).
                        cooldown_ok = (last_naked_try[heavy] is None
                                       or (now - last_naked_try[heavy]) >= REQUOTE_SEC * 3)
                        if due and cooldown_ok:
                            last_naked_try[heavy] = now
                            # thresh=1: complete_pairs acts on ANY naked (>=1 share),
                            # not just at naked_cap; legacy auto_flat uses naked_cap.
                            thresh = 1 if self.cfg.complete_pairs else self.cfg.naked_cap
                            a = plan_naked_action(inv_yes, inv_no, inv.avg("YES"),
                                                  inv.avg("NO"), yes_ask, no_ask, thresh)
                            completed = False
                            # Don't pair off a DELIBERATE directional tilt: when the heavy
                            # side is the favorite (tbias direction), completing buys the
                            # loser and unwinds the net-long-favorite edge — let it ride.
                            # When the LOSER is heavy, completion buys the favorite cheap
                            # (aligned + merge edge) and proceeds normally.
                            tilt_heavy = (self.cfg.tilt_enabled and tbias != "NEUTRAL"
                                          and heavy == ("YES" if tbias == "UP" else "NO"))
                            if a and a.kind == "COMPLETE" and not tilt_heavy:
                                # tighter cap: complete only enough to BALANCE the pair
                                # (light never exceeds heavy), accounting for completes
                                # the lagging inventory hasn't absorbed yet -> zero excess
                                # naked. complete_cap_qty stays as a hard lag-proof backstop.
                                if self.cfg.complete_continuous:
                                    # CONTINUOUS: small step per shot, subtract only RECENT
                                    # completes (within feed lag) so we can keep topping up
                                    # new naked through the window. SELL stays near-end only.
                                    recent = recent_complete_qty(complete_log[a.side], now,
                                                                 self.cfg.inv_reconcile_grace_sec)
                                    cq = min(balance_complete_qty(a.qty, recent),
                                             float(self.cfg.complete_step))
                                else:
                                    cq = min(balance_complete_qty(a.qty, completed_taker[a.side]),
                                             complete_cap_qty(a.qty, completed_taker[a.side],
                                                              self.cfg.naked_cap + self.cfg.rung_size))
                                tok = m.yes_token if a.side == "YES" else m.no_token
                                px = yes_ask if a.side == "YES" else no_ask
                                # Money bound: the completion is a TAKER buy and is NOT
                                # gated by the per-post per_window_cap check below, so cap
                                # its qty by the remaining $ budget — otherwise deep mode
                                # (large naked cheap leg) could complete past per_window_cap.
                                if px and px > 0:
                                    budget_left = self.cfg.per_window_cap - realized
                                    cq = float(int(min(cq, max(0.0, budget_left / px))))
                                if px and cq > 0:
                                    r = await self._place_limit(
                                        token_id=tok, price=px, size=cq,
                                        side="BUY", post_only=False, order_type="FOK")
                                    if r and r.get("order_id"):
                                        completed = True
                                        completed_taker[a.side] += cq
                                        # account completion spend in the SAME tick so the
                                        # later tilt gate (realized) and base gate (committed)
                                        # don't double-spend against one stale collateral
                                        # snapshot. Collateral re-read corrects it next tick.
                                        realized += cq * px
                                        committed += cq * px
                                        if self.cfg.complete_continuous:
                                            complete_log[a.side].append((now, cq))
                                        naked_since[heavy] = None
                                        log.info("runner_ladder_complete", slug=m.slug,
                                                 side=a.side, qty=cq, price=round(px, 3))
                            # a COMPLETE that was capped to 0 (already completed enough)
                            # is NOT a failed completion — don't fall through to SELL.
                            complete_capped = (not self.cfg.complete_continuous
                                               and a is not None and a.kind == "COMPLETE"
                                               and completed_taker[a.side] >= self.cfg.naked_cap + self.cfg.rung_size)
                            # SELL gating. sell_fallback=False (guru-style) => NEVER sell: hold
                            # the cheap residual to resolution (no FOK-in-no-bid loss path).
                            # CONTINUOUS: even with sell_fallback, only sell near-end (never dump
                            # the cheap leg mid-window — pair it later instead).
                            near_end_sell = near_end if self.cfg.complete_continuous else True
                            # never SELL a deliberate favorite tilt either (defends the
                            # net-long edge even if sell_fallback is ever enabled).
                            may_sell = self.cfg.sell_fallback and near_end_sell and not tilt_heavy
                            if a and not tilt_heavy and ((a.kind == "SELL" and may_sell)
                                      or (not completed and not complete_capped
                                          and near_end and self.cfg.sell_fallback)):
                                bid = yes_bid if heavy == "YES" else no_bid
                                tok = m.yes_token if heavy == "YES" else m.no_token
                                qty = abs(naked)
                                if bid:
                                    r = await self._place_limit(
                                        token_id=tok, price=bid, size=qty,
                                        side="SELL", post_only=False, order_type="FOK")
                                    if r and r.get("order_id"):
                                        flattened.add(heavy)
                                        log.info("runner_ladder_flatten", slug=m.slug,
                                                 side=heavy, qty=qty, price=round(bid, 3))
                    elif heavy and abs(naked) < self.cfg.naked_cap:
                        naked_since[heavy] = None

                # track shadow entry for the circuit-breaker (even when tilt is paused)
                if tbias != "NEUTRAL":
                    last_bias = tbias
                    fav_ask_now = yes_ask if tbias == "UP" else no_ask
                    if fav_ask_now:
                        last_fav_entry = fav_ask_now
                if yes_bid and yes_ask:
                    last_mid = (yes_bid + yes_ask) / 2

                # DIRECTIONAL TILT: taker-buy the favorite when the trend is confirmed
                # AND the circuit-breaker says the edge is alive. Base stays two-sided
                # (insurance leg) — tilt is the only thing that uses the bias.
                if (self.cfg.tilt_enabled and tbias != "NEUTRAL"
                        and self._regime.directional_enabled()
                        and m.time_remaining() > self.cfg.tilt_cutoff_sec):
                    fav_side = "YES" if tbias == "UP" else "NO"
                    fav_ask = yes_ask if fav_side == "YES" else no_ask
                    fav_cost = inv.cost["YES"] if fav_side == "YES" else inv.cost["NO"]
                    cq = plan_tilt(tbias, fav_ask, fav_cost, realized,
                                   self.cfg.per_window_cap, self.cfg.tilt_frac,
                                   self.cfg.complete_step, self.cfg.tilt_max_price)
                    if cq > 0 and fav_ask:
                        tok = m.yes_token if fav_side == "YES" else m.no_token
                        r = await self._place_limit(
                            token_id=tok, price=fav_ask, size=cq,
                            side="BUY", post_only=False, order_type="FAK")
                        if r and r.get("order_id"):
                            posted[fav_side] += cq
                            # account the tilt spend so the SAME-tick base-ladder
                            # budget gate (committed + post_cost <= per_window_cap)
                            # sees it; collateral re-read corrects it next tick.
                            # Intended cost (cq*fav_ask): over-counts on partial FAK
                            # fills, which is fail-safe for a cap.
                            committed += cq * fav_ask
                            log.info("runner_tilt", slug=m.slug, side=fav_side,
                                     qty=cq, price=round(fav_ask, 3))

                self.state.last_event = f"laddering {m.slug} (bias {tbias})"
                # base ladder: NEUTRAL → never trend-suppress the loser (= insurance leg)
                plan = plan_ladder(
                    yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
                    entry_mid=entry_mid, inv_yes=inv_yes, inv_no=inv_no,
                    yes_cost=inv.cost["YES"], no_cost=inv.cost["NO"],
                    committed=committed, resting=resting, cfg=self.cfg,
                    trend_bias="NEUTRAL", suppressed=frozenset(flattened))

                if plan.cancels:
                    await self._cancel_orders(plan.cancels)
                    for side in ("YES", "NO"):
                        resting[side] = [ro for ro in resting[side] if ro.order_id not in plan.cancels]

                for q in plan.posts:
                    if not inv_ok:
                        break               # inventory unknown this tick -> don't post
                    # Lag-proof sweep backstop: never post a side past the cap on
                    # cumulative posted shares. This bounds worst-case naked even
                    # if the inventory count is wrong, so a reset can no longer
                    # re-open posting and sweep (window-6 bug).
                    if posted[q.side] + q.size > post_cap + 1e-9:
                        continue
                    post_cost = q.price * q.size
                    if committed + post_cost > self.cfg.per_window_cap + 1e-9:
                        continue
                    tok = m.yes_token if q.side == "YES" else m.no_token
                    r = await self._place_limit(token_id=tok, price=q.price, size=q.size,
                                                    side="BUY", post_only=True)
                    if r and r.get("order_id"):
                        resting[q.side].append(RestingOrder(r["order_id"], q.side, q.price, q.size))
                        placed_at[r["order_id"]] = now
                        last_px[q.side] = q.price
                        committed += post_cost
                        posted[q.side] += q.size

                self.state.pairs_caught = min(inv_yes, inv_no)
                self.state.naked_shares = abs(inv_yes - inv_no)
                await asyncio.sleep(REQUOTE_SEC)
        finally:
            await self.cancel_all()

        iy = max(self._shares(m.yes_token), local.inv["YES"])
        inn = max(self._shares(m.no_token), local.inv["NO"])
        self.state.last_event = f"ladder done: matched={min(iy, inn)} naked={abs(iy - inn)}"
        log.info("runner_ladder_done", slug=m.slug, matched=min(iy, inn), naked=abs(iy - inn))
        if last_bias != "NEUTRAL":
            winner = "Up" if last_mid >= 0.5 else "Down"
            pred = "Up" if last_bias == "UP" else "Down"
            self._regime.record(pred, last_fav_entry, winner)
            log.info("runner_regime_record", slug=m.slug, pred=pred,
                     entry=round(last_fav_entry, 3), winner=winner,
                     paper_ev=round(self._regime.paper_ev() or 0.0, 4),
                     enabled=self._regime.directional_enabled())

        # MEASUREMENT: the whole point of deep mode — at what price did WE actually catch
        # each leg, and is the assembled pair < $1? (guru's cheap leg ≈ $0.07, pair ≈ $0.96)
        avg_yes = (local.cost["YES"] / local.inv["YES"]) if local.inv["YES"] > 0 else None
        avg_no = (local.cost["NO"] / local.inv["NO"]) if local.inv["NO"] > 0 else None
        pairs = min(local.inv["YES"], local.inv["NO"])
        total_cost = local.cost["YES"] + local.cost["NO"]
        pair_cost = (total_cost / pairs) if pairs > 0 else None
        cheap_leg = None
        cheap_avg = None
        if avg_yes is not None and avg_no is not None:
            cheap_leg, cheap_avg = ("YES", avg_yes) if avg_yes <= avg_no else ("NO", avg_no)
        log.info("runner_ladder_fillquality", slug=m.slug,
                 inv_yes=local.inv["YES"], inv_no=local.inv["NO"],
                 avg_yes=round(avg_yes, 3) if avg_yes is not None else None,
                 avg_no=round(avg_no, 3) if avg_no is not None else None,
                 cheap_leg=cheap_leg,
                 cheap_avg=round(cheap_avg, 3) if cheap_avg is not None else None,
                 pairs=pairs,
                 pair_cost=round(pair_cost, 3) if pair_cost is not None else None,
                 spent=round(total_cost, 2),
                 rtt_ms_min=round(min(rtt_samples), 1) if rtt_samples else None,
                 rtt_ms_avg=round(sum(rtt_samples) / len(rtt_samples), 1) if rtt_samples else None)

    async def _five_min_window(self, m, mid_at_entry: float) -> None:
        """5m early-consistent-leader (dry-run + paper-fill). Accumulate a leader-leaned
        two-sided maker position from the open; at minute 2 keep only if the leader was
        consistent (min1==min2) and in-band; hold to resolution; estimate PnL via PaperBook."""
        self._traded_windows.add(m.open_ts)
        self.state.windows_traded += 1
        self.state.last_window = m.slug
        log.info("fivemin_enter", slug=m.slug, mid=round(mid_at_entry, 3))

        pb = PaperBook(fill_frac=max(0.1, self.cfg.paper_fill_prob_multiplier))
        lead1 = lead2 = None
        lead_price2 = 0.0
        last_yes_mid = mid_at_entry
        try:
            while m.time_remaining() > END_BUFFER_SEC:
                if self.state.force_stop_requested:
                    break
                try:
                    async with httpx.AsyncClient(timeout=6) as cl:
                        by = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.yes_token})).json()
                        bn = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.no_token})).json()
                except Exception:
                    await asyncio.sleep(REQUOTE_SEC); continue
                yb, ya = _best(by, "bids"), _best(by, "asks")
                nb, na = _best(bn, "bids"), _best(bn, "asks")
                if not (yb and ya and nb and na):
                    await asyncio.sleep(REQUOTE_SEC); continue
                yes_mid = (yb + ya) / 2
                no_mid = (nb + na) / 2
                last_yes_mid = yes_mid
                minute = (time.time() - m.open_ts) / 60.0   # minutes since window open (wall clock)

                cur_leader = "YES" if yes_mid >= no_mid else "NO"
                if lead1 is None and minute >= 1:
                    lead1 = cur_leader
                if lead2 is None and minute >= 2:
                    lead2 = cur_leader
                    lead_price2 = yes_mid if cur_leader == "YES" else no_mid

                plan = plan_five_min(minute, lead1, lead2, lead_price2, pb.posted(),
                                     self.cfg.per_window_cap, self.cfg.lean,
                                     self.cfg.band_lo, self.cfg.band_hi,
                                     self.cfg.rung_size, yes_mid, no_mid)
                for side, price, size in plan.orders:
                    tok = m.yes_token if side == "YES" else m.no_token
                    await self._place_limit(token_id=tok, price=price, size=size,
                                            side="BUY", post_only=True)   # dry-run: logs only
                    pb.post(side, price, size)

                pb.on_tick("YES", ya)
                pb.on_tick("NO", na)
                self.state.pairs_caught = int(min(pb.inv["YES"], pb.inv["NO"]))
                self.state.naked_shares = int(abs(pb.inv["YES"] - pb.inv["NO"]))
                await asyncio.sleep(REQUOTE_SEC)
        finally:
            await self.cancel_all()   # dry-run no-op

        winner = "YES" if last_yes_mid >= 0.5 else "NO"
        paper_pnl = pb.inv[winner] - pb.spent()
        # full traded gate (matches plan_five_min): consistent leader AND in-band —
        # so paper stats can be sliced by the REAL selection the tactic rests on.
        passed = bool(lead1 is not None and lead1 == lead2
                      and self.cfg.band_lo <= lead_price2 <= self.cfg.band_hi)
        log.info("fivemin_done", slug=m.slug, passed=passed, winner=winner,
                 paper_spent=round(pb.spent(), 2), paper_pnl=round(paper_pnl, 2),
                 inv_yes=round(pb.inv["YES"], 1), inv_no=round(pb.inv["NO"], 1))

    async def _top_book_window(self, m, mid_at_entry: float) -> None:
        """Top-of-book MM (phase-27): keep bid best+tick on BOTH sides, skew-cap naked,
        merge matched pairs (via positions_ops when live; logged intent in dry-run),
        never sell. Spend bounded by per_window_cap on COMMITTED capital = realized
        cost basis (never decremented, not even after merge — bounds worst-case gross
        spend if merge fails) + resting notional."""
        self._traded_windows.add(m.open_ts)
        self.state.windows_traded += 1
        self.state.last_window = m.slug
        self.state.last_event = f"topbook {m.slug}"
        self.state.fills_window = 0.0
        self.state.matched_pct = 0.0
        log.info("topbook_enter", slug=m.slug, mid=round(mid_at_entry, 3))
        inv = {"Up": 0.0, "Down": 0.0}     # filled (credit-on-vanish + partial credit)
        cost = {"Up": 0.0, "Down": 0.0}    # realized cost basis — NEVER decremented
        resting: dict[str, tuple[float, float]] = {}   # side -> (price, size)
        oids: dict[str, list[str]] = {"Up": [], "Down": []}
        merged = 0.0
        tok = {"Up": m.yes_token, "Down": m.no_token}
        try:
            async with httpx.AsyncClient(timeout=8) as cl:
                while m.time_remaining() > END_BUFFER_SEC and not self._shutdown:
                    if self.state.drain_force_stop():
                        await self.cancel_all()   # immediate; finally is the backstop
                        return
                    try:
                        by = (await cl.get("https://clob.polymarket.com/book", params={"token_id": tok["Up"]})).json()
                        bn = (await cl.get("https://clob.polymarket.com/book", params={"token_id": tok["Down"]})).json()
                    except Exception:
                        await asyncio.sleep(REQUOTE_SEC)
                        continue
                    # The whole mutate section (cancels + posts + fill-credit + merge)
                    # is try/excepted like the book GETs: one transient API error must
                    # not abort the window; the finally cancel_all stays the backstop.
                    try:
                        target = plan_top_book(by, bn, inv["Up"], inv["Down"],
                                               self.cfg.tb_naked_cap, self.cfg.tb_size, self.cfg.tb_tick)
                        cancel, post = diff_quotes(resting, target)
                        for side in cancel:
                            # partial fills: credit the matched portion BEFORE clearing
                            # so inv/cost/merge/skew see reality (live only; lookup
                            # failure -> no credit, same as before).
                            if not self.cfg.dry_run and side in resting and oids.get(side):
                                p, _sz = resting[side]
                                matched = self._order_matched(oids[side][0])
                                if matched:
                                    inv[side] += matched
                                    cost[side] += matched * p
                                    log.info("topbook_partial_fill", side=side, price=p,
                                             matched=matched)
                            await self._cancel_orders(oids.get(side, []))
                            oids[side] = []
                            resting.pop(side, None)
                        for q in post:
                            other = "Down" if q.side == "Up" else "Up"
                            # HARD skew re-check against MID-TICK inventory. The plan gate
                            # ran on tick-top inv, but the cancel loop above may have just
                            # credited a full fill of the repriced order (partial-credit),
                            # so inv[q.side] can already be at cap here. Without this a
                            # reprice-during-trend would rest a fresh size-`size` order on
                            # top of at-cap inventory -> naked up to cap+size (the residual
                            # overshoot found in review). Mirrors the committed_gate re-check.
                            if not skew_ok(inv[q.side], inv[other], q.size,
                                           self.cfg.tb_naked_cap):
                                continue
                            # committed-capital gate, re-checked per post: each approved
                            # quote lands in `resting` (counted below) before the next
                            # check, so several same-tick posts can't jointly overshoot.
                            if not committed_gate(cost["Up"], cost["Down"], resting, q,
                                                  self.cfg.per_window_cap):
                                continue
                            r = await self._place_limit(token_id=tok[q.side], price=q.price,
                                                        size=q.size, side="BUY", post_only=True)
                            if self.cfg.dry_run:
                                # placement is a logged no-op — resting tracks INTENT or
                                # the diff would re-post (and re-log) every tick.
                                resting[q.side] = (q.price, q.size)
                            elif r and r.get("order_id"):
                                # live: only a real order id becomes resting/committed —
                                # a failed placement must not leave a phantom quote.
                                oids[q.side] = [r["order_id"]]
                                resting[q.side] = (q.price, q.size)
                        # fills: credit-on-vanish (LIVE) — in dry_run open-orders are empty
                        # and resting was never real, so nothing credits (mechanics-only).
                        if not self.cfg.dry_run:
                            open_ids = self._open_order_ids()
                            for side in ("Up", "Down"):
                                gone = [o for o in oids[side] if o not in open_ids]
                                if gone and side in resting:
                                    p, sz = resting.pop(side)
                                    oids[side] = []
                                    matched = self._order_matched(gone[0])
                                    if matched is None:
                                        # status lookup failed: assume FULL fill — over-
                                        # crediting inflates cost, so the cap only ever
                                        # TIGHTENS (fail-conservative for spend).
                                        matched = sz
                                        log.info("topbook_fill_assumed", side=side, price=p,
                                                 size=sz)
                                    if matched > 0:
                                        inv[side] += matched
                                        cost[side] += matched * p
                                        log.info("topbook_fill", side=side, price=p,
                                                 size=matched)
                        # merge matched pairs (cost basis intentionally NOT reduced)
                        mq = plan_merge(inv["Up"], inv["Down"], self.cfg.tb_merge_min)
                        if mq > 0:
                            ok = await self._merge_pairs(m, mq)   # dry_run -> logs intent, True
                            if ok:
                                inv["Up"] -= mq
                                inv["Down"] -= mq
                                merged += mq
                                self.state.merged_today += mq
                        self.state.pairs_caught = int(min(inv["Up"], inv["Down"]))
                        self.state.naked_shares = int(abs(inv["Up"] - inv["Down"]))
                        self.state.fills_window = inv["Up"] + inv["Down"] + 2 * merged
                        self.state.matched_pct = (100 * 2 * merged
                                                  / max(self.state.fills_window, 1e-9))
                        # exact per-tick quote state — the shadow-fill harness's ground
                        # truth (a place-only log can't see cancels/gates and would credit
                        # us stale toxic fills the live bot dodges)
                        log.info("topbook_quotes", slug=m.slug,
                                 up=(resting.get("Up") or (None, None))[0],
                                 dn=(resting.get("Down") or (None, None))[0])
                    except Exception as e:
                        log.warning("topbook_tick_err", error=str(e))
                    await asyncio.sleep(REQUOTE_SEC)
        finally:
            await self.cancel_all()
        committed = cost["Up"] + cost["Down"] + sum(p * sz for (p, sz) in resting.values())
        log.info("topbook_done", slug=m.slug, merged=merged,
                 inv_up=inv["Up"], inv_dn=inv["Down"],
                 spent=round(cost["Up"] + cost["Down"], 2), committed=round(committed, 2))

    async def _redeem_sweeper(self) -> None:
        """Every 60s: redeem resolved positions so capital returns to cash, then wrap
        any stranded USDC.e back into pUSD (merge/redeem return USDC.e, but the CLOB
        trades with pUSD — without the wrap that capital never re-enters the quote
        balance). Dry-run: log only. Isolated loop — its failure never stops quoting
        (every pass is fully try/excepted)."""
        while not self._shutdown:
            try:
                async with httpx.AsyncClient(timeout=10) as cl:
                    r = await cl.get("https://data-api.polymarket.com/positions",
                                     params={"user": self.creds.funder, "redeemable": "true",
                                             "sizeThreshold": 1, "limit": 100})
                    for p in (r.json() if r.status_code == 200 else []):
                        if self.cfg.dry_run:
                            log.info("dryrun_redeem", slug=p.get("slug"), size=p.get("size"))
                            continue
                        from quoter.chain.positions_ops import redeem
                        if redeem(p.get("conditionId")):
                            # dollar value returned to cash (losing side redeems ~$0),
                            # not share count — the metric is USD recovered.
                            self.state.redeemed_today += float(p.get("currentValue", 0) or 0)
                if not self.cfg.dry_run:
                    from quoter.chain.positions_ops import (
                        usdce_balance, wrap_usdce_to_pusd, wrap_decision)
                    bal = usdce_balance()  # read-only; None = unknown
                    do_wrap, reason = wrap_decision(bal, WRAP_MIN_USD)
                    if do_wrap:
                        ok = wrap_usdce_to_pusd(bal)
                        log.info("wrap_result", usdce=round(bal, 2), ok=ok)
                    else:
                        # never silent: a stranded balance now leaves a trace of WHY
                        # (balance_unknown = RPC read failed; below_min = nothing to do)
                        log.info("wrap_skip", reason=reason,
                                 usdce=(round(bal, 2) if bal is not None else None))
            except Exception as e:
                log.warning("redeem_sweeper_err", error=str(e))
            await asyncio.sleep(60)

    async def _merge_pairs(self, m, qty: float) -> bool:
        """Merge qty matched Up/Down pairs back to USDC. Dry-run: log intent only."""
        if self.cfg.dry_run:
            log.info("dryrun_merge", slug=m.slug, qty=qty)
            return True
        from quoter.chain.positions_ops import merge_pairs
        try:
            return merge_pairs(m.market_id, qty)
        except Exception as e:
            log.warning("merge_failed", error=str(e))
            return False

    def shutdown(self) -> None:
        self._shutdown = True
