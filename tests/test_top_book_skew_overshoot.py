"""Integration regression test for the hard-skew overshoot found in the 2026-07-05
attended live test (cap 10 reached 14/20 naked in a trending window) and the residual
reprice-path hole found in review.

Drives one real `_top_book_window` loop through a *trending* book where our Up quote
must reprice every tick (bid keeps rising) and every Up order fills fully — the exact
regime that made a fill get credited MID-tick via the cancel-and-reprice path, after
which the OLD code re-posted a fresh size-5 order on top of at-cap inventory (naked ->
cap+size = 15). The hard skew re-check in the post loop must hold naked <= cap.
"""
import asyncio

import pytest

from quoter.config import Config
from quoter.runner import merge_runner
from quoter.runner.merge_runner import MergeRunner
from quoter.runner.trading_state import TradingState


class _Ctl:
    def __init__(self):
        self.tick = 0           # advances on each (patched) sleep -> Up bid rises
        self.naked_log = []     # state.naked_shares recorded at the end of every tick
        self.open = set()       # order ids currently resting on the (fake) book
        self.oid_seq = 0
        self.places = 0         # total _place_limit calls


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _book(bid, ask=0.99):
    return {"bids": [{"price": f"{bid:.3f}", "size": "100"}],
            "asks": [{"price": f"{ask:.3f}", "size": "100"}]}


class _M:
    slug = "btc-updown-5m-trend"
    yes_token = "UP"
    no_token = "DN"
    open_ts = 1

    def time_remaining(self):
        return 100.0            # always > END_BUFFER_SEC; loop exits via _shutdown


def _make_runner(ctl):
    r = MergeRunner.__new__(MergeRunner)      # bypass __init__ (needs creds/network)
    r.cfg = Config(
        strategy="top_book", assets=("BTC",), timeframes=("5m",),
        tb_size=5.0, tb_naked_cap=10.0, tb_tick=0.001, tb_merge_min=5.0,
        per_window_cap=15.0, per_market_cap_usd=15.0, dry_run=False,   # LIVE credit path
    )
    r.state = TradingState()
    r._shutdown = False
    r._traded_windows = set()

    async def fake_place(**kw):
        ctl.oid_seq += 1
        ctl.places += 1
        oid = f"o{ctl.oid_seq}"
        ctl.open.add(oid)
        return {"order_id": oid, "status": "live"}

    async def fake_cancel(oids):
        for o in oids:
            ctl.open.discard(o)

    async def fake_cancel_all():
        ctl.open.clear()
        return 0

    async def fake_merge(m, qty):
        return True

    r._place_limit = fake_place
    r._cancel_orders = fake_cancel
    r._open_order_ids = lambda: set(ctl.open)
    r._order_matched = lambda oid: 5.0        # every cancelled Up order was fully filled
    r._merge_pairs = fake_merge
    r.cancel_all = fake_cancel_all
    return r


def test_reprice_trend_never_overshoots_cap(monkeypatch):
    ctl = _Ctl()
    runner = _make_runner(ctl)

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            if params["token_id"] == "UP":
                return _Resp(_book(0.50 + 0.01 * ctl.tick))   # rising -> reprice each tick
            return _Resp(_book(0.45))                          # Down flat, never fills

    async def fake_sleep(_):
        ctl.naked_log.append(runner.state.naked_shares)
        ctl.tick += 1
        if ctl.tick >= 5:
            runner._shutdown = True

    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", lambda *a, **k: _FakeClient())
    monkeypatch.setattr(merge_runner.asyncio, "sleep", fake_sleep)

    asyncio.run(runner._top_book_window(_M(), 0.5))

    # The core safety invariant: naked never crosses the cap. Pre-fix (no post-loop
    # re-check) this list would contain 15 (cap 10 + size 5) via the reprice-credit path.
    assert ctl.naked_log, "loop never ran a tick"
    assert max(ctl.naked_log) <= runner.cfg.tb_naked_cap, ctl.naked_log
    # And it genuinely exercised the danger zone — inventory built up to exactly the cap
    # through the mid-tick reprice-credit, proving the re-check (not luck) held the line.
    assert max(ctl.naked_log) == runner.cfg.tb_naked_cap, ctl.naked_log
