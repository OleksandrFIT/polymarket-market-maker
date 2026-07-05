"""Integration tests for top_book near-end naked handling:
  - COMPLETE the pair (buy light leg) when it costs < $1 -> zero naked (calm windows)
  - self-funding budget headroom so completion fires even when the maker $ cap is exhausted
  - SELL the loser (heavy leg) when the pair >= $1 (trend) instead of riding to resolution
Plus the two bugs a review caught in the first cut: a killed FOK must not phantom-close a
leg, and a cumulative completed-counter must not permanently suppress genuinely-new naked.
"""
import asyncio

from quoter.config import Config
from quoter.runner import merge_runner
from quoter.runner.merge_runner import MergeRunner
from quoter.runner.trading_state import TradingState


class _Ctl:
    def __init__(self):
        self.t_remaining = 100.0
        self.tick = 0
        self.clock = 1000.0
        self.dt = 0.0
        self.places = []           # (token, side, price, size, post_only, order_type)
        self.open = set()
        self.kind = {}
        self.size = {}             # oid -> requested size (FOK matches its own size, or 0 if killed)
        self.seq = 0


class _Resp:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d


def _book(bid, ask):
    return {"bids": [{"price": f"{bid:.3f}", "size": "500"}],
            "asks": [{"price": f"{ask:.3f}", "size": "500"}]}


class _M:
    slug = "btc-updown-5m-x"
    yes_token = "UP"
    no_token = "DN"
    open_ts = 1

    def __init__(self, ctl):
        self._ctl = ctl

    def time_remaining(self):
        return self._ctl.t_remaining


def _make_runner(ctl, fok_fills=True, per_window_cap=15.0, complete_budget=0.0,
                 tb_sell_naked=False):
    r = MergeRunner.__new__(MergeRunner)
    r.cfg = Config(
        strategy="top_book", assets=("BTC",), timeframes=("5m",),
        tb_size=5.0, tb_naked_cap=6.0, tb_tick=0.001, tb_merge_min=5.0,
        per_window_cap=per_window_cap, tb_complete=True, tb_complete_gate_sec=45.0,
        complete_budget=complete_budget, tb_sell_naked=tb_sell_naked,
        inv_reconcile_grace_sec=12.0, dry_run=False,
    )
    r.state = TradingState()
    r._shutdown = False
    r._traded_windows = set()

    async def fake_place(**kw):
        ctl.seq += 1
        oid = f"o{ctl.seq}"
        ctl.kind[oid] = "FOK" if kw.get("order_type") == "FOK" else "maker"
        ctl.size[oid] = kw["size"]
        ctl.places.append((kw["token_id"], kw["side"], kw["price"], kw["size"],
                           kw.get("post_only"), kw.get("order_type")))
        if kw.get("post_only") and kw["token_id"] == "DN":
            ctl.open.add(oid)          # Down maker rests (no fill); Up maker vanishes (filled)
        return {"order_id": oid, "status": "live"}

    async def fake_cancel(oids):
        for o in oids:
            ctl.open.discard(o)

    async def fake_cancel_all():
        return 0

    async def fake_merge(m, qty):
        return True

    def order_matched(oid):
        # a real FOK matches its OWN size (all-or-nothing) or 0 if killed; a maker order
        # matches its resting size. Returning a fixed number regardless of size would let a
        # budget-shrunk order over-credit — the exact bug this test must not hide.
        if ctl.kind.get(oid) == "FOK":
            return ctl.size.get(oid, 0.0) if fok_fills else 0.0
        return ctl.size.get(oid, 5.0)

    r._place_limit = fake_place
    r._cancel_orders = fake_cancel
    r._open_order_ids = lambda: set(ctl.open)
    r._order_matched = order_matched
    r._merge_pairs = fake_merge
    r.cancel_all = fake_cancel_all
    return r


def _run(ctl, runner, n_ticks, monkeypatch, up=(0.50, 0.99), dn=(0.01, 0.45)):
    class _Cl:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _Resp(_book(*up)) if params["token_id"] == "UP" else _Resp(_book(*dn))

    async def fake_sleep(_):
        ctl.tick += 1
        ctl.clock += ctl.dt
        ctl.t_remaining = 40.0
        if ctl.tick >= n_ticks:
            runner._shutdown = True

    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", lambda *a, **k: _Cl())
    monkeypatch.setattr(merge_runner.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(merge_runner, "monotonic", lambda: ctl.clock)
    asyncio.run(runner._top_book_window(_M(ctl), 0.5))


def _fok_buys(ctl, token):
    return [p for p in ctl.places if p[0] == token and p[1] == "BUY" and p[5] == "FOK"]


def _fok_sells(ctl, token):
    return [p for p in ctl.places if p[0] == token and p[1] == "SELL" and p[5] == "FOK"]


def test_near_end_completes_naked_pair_to_zero(monkeypatch):
    ctl = _Ctl()
    runner = _make_runner(ctl)
    _run(ctl, runner, 2, monkeypatch)
    assert _fok_buys(ctl, "DN") and _fok_buys(ctl, "DN")[0][3] == 5.0
    assert runner.state.naked_shares == 0


def test_killed_fok_does_not_phantom_close(monkeypatch):
    ctl = _Ctl()
    runner = _make_runner(ctl, fok_fills=False)          # FOK killed -> no fill
    _run(ctl, runner, 2, monkeypatch)
    assert _fok_buys(ctl, "DN"), "completion should still attempt the FOK"
    assert runner.state.naked_shares == 5                # naked NOT phantom-closed


def test_completion_not_suppressed_after_merge(monkeypatch):
    ctl = _Ctl()
    ctl.dt = 20.0                                         # age each completion past 12s grace
    runner = _make_runner(ctl)
    _run(ctl, runner, 3, monkeypatch)
    assert len(_fok_buys(ctl, "DN")) >= 2
    assert runner.state.naked_shares == 0


def test_cap_exhausted_needs_budget_to_fully_close(monkeypatch):
    # per_window_cap 3.0: after Up fills (cost 2.5) the maker $ budget is nearly spent, so
    # WITHOUT complete_budget the completion can only buy ~1 Down -> naked NOT fully closed.
    ctl = _Ctl()
    runner = _make_runner(ctl, per_window_cap=3.0, complete_budget=0.0)
    _run(ctl, runner, 2, monkeypatch)
    assert runner.state.naked_shares > 0                 # couldn't fully complete (budget-starved)


def test_budget_headroom_closes_cap_frozen_naked(monkeypatch):
    # same cap 3.0, but complete_budget 6.0 gives self-funding headroom -> full completion.
    ctl = _Ctl()
    runner = _make_runner(ctl, per_window_cap=3.0, complete_budget=6.0)
    _run(ctl, runner, 2, monkeypatch)
    assert _fok_buys(ctl, "DN") and _fok_buys(ctl, "DN")[0][3] == 5.0
    assert runner.state.naked_shares == 0


def test_trend_sells_loser_when_pair_over_dollar(monkeypatch):
    # Down ask expensive (0.85): heavy_avg 0.5 + 0.85 = 1.35 >= $1 -> completing would lock a
    # bigger loss. With tb_sell_naked the loop SELLS the heavy Up loser into its bid instead.
    ctl = _Ctl()
    runner = _make_runner(ctl, tb_sell_naked=True)
    _run(ctl, runner, 2, monkeypatch, up=(0.50, 0.99), dn=(0.01, 0.85))
    assert not _fok_buys(ctl, "DN"), "must NOT complete an over-$1 pair"
    assert _fok_sells(ctl, "UP") and _fok_sells(ctl, "UP")[0][3] == 5.0
    assert runner.state.naked_shares == 0                # loser sold -> zero naked
