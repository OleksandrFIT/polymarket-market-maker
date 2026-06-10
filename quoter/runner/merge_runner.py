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
                 requote: bool = False):
        self.creds = creds
        self.cfg = cfg
        self.state = state
        self.requote = requote
        self.clob = ClobOps(creds)
        self._rc = None  # lazy read client (py_clob_client_v2)
        self._traded_windows: set[int] = set()
        self._shutdown = False

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
                        if self.requote:
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
                if not yes_bid or not no_bid:
                    await asyncio.sleep(REQUOTE_SEC)
                    continue
                inv_yes, inv_no = self._shares(m.yes_token), self._shares(m.no_token)
                coll_now = self.collateral_usd()
                now = monotonic()

                # Reconcile tracked orders with reality, but only DROP an order
                # that's been gone for > one cadence — a just-placed order can be
                # missing from a stale get_open_orders read (avoids double-posting).
                open_ids = self._open_order_ids()
                for side in ("YES", "NO"):
                    ro = resting[side]
                    if ro is not None and ro.order_id not in open_ids and (now - placed_at[side]) > REQUOTE_SEC:
                        resting[side] = None

                # FORWARD-LOOKING spend: realized (balance-delta OR inventory est,
                # whichever larger — fail-safe) PLUS the value of orders still
                # resting. We only post if committed + new_post stays within the
                # cap, so realized spend can never exceed per_market_cap_usd.
                spent_bal = (coll_start - coll_now) if (coll_start >= 0 and coll_now >= 0) else 0.0
                spent_inv = inv_yes * yes_bid + inv_no * no_bid
                resting_val = sum(ro.price * ro.size for ro in resting.values() if ro is not None)
                committed = max(0.0, spent_bal, spent_inv) + resting_val

                plan = plan_requote(
                    yes_bid=yes_bid, no_bid=no_bid, inv_yes=inv_yes, inv_no=inv_no,
                    yes_cost=committed, no_cost=0.0, resting=resting, cfg=self.cfg)

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

                self.state.pairs_caught = min(inv_yes, inv_no)
                self.state.naked_shares = abs(inv_yes - inv_no)
                await asyncio.sleep(REQUOTE_SEC)
        finally:
            # Robust cleanup on graceful end, FORCE STOP, or any exception:
            # cancel ALL our resting orders account-wide (covers any order dropped
            # from local tracking). Held positions/pairs are untouched.
            await self.cancel_all()

        iy, inn = self._shares(m.yes_token), self._shares(m.no_token)
        self.state.last_event = f"re-quote done: matched={min(iy, inn)} naked={abs(iy - inn)}"
        log.info("runner_requote_done", slug=m.slug, matched=min(iy, inn), naked=abs(iy - inn))

    def shutdown(self) -> None:
        self._shutdown = True
