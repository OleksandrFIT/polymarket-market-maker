"""topbook_fillquality telemetry: realized pair_cost of merged pairs + naked residual outcome.
Log-only instrumentation — asserts the emitted measurement event, no behavior change."""
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
        self.places = []
        self.open = set()
        self.kind = {}
        self.size = {}
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


def _make_runner(ctl, fok_fills=True):
    r = MergeRunner.__new__(MergeRunner)
    r.cfg = Config(
        strategy="top_book", assets=("BTC",), timeframes=("5m",),
        tb_size=5.0, tb_naked_cap=6.0, tb_tick=0.001, tb_merge_min=5.0,
        per_window_cap=15.0, tb_complete=True, tb_complete_gate_sec=45.0,
        complete_budget=6.0, inv_reconcile_grace_sec=12.0, dry_run=False,
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
            ctl.open.add(oid)
        return {"order_id": oid, "status": "live"}

    async def fake_cancel(oids):
        for o in oids:
            ctl.open.discard(o)

    async def fake_cancel_all():
        return 0

    async def fake_merge(m, qty):
        return True

    def order_matched(oid):
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


def _capture(monkeypatch):
    events = []

    class _L:
        def info(self, ev, **kw):
            events.append((ev, kw))

        def warning(self, ev, **kw):
            pass

    monkeypatch.setattr(merge_runner, "log", _L())
    return events


def _run(ctl, runner, n_ticks, monkeypatch, up=(0.50, 0.99), dn=(0.01, 0.45), t_after=40.0):
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
        ctl.t_remaining = t_after
        if ctl.tick >= n_ticks:
            runner._shutdown = True

    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", lambda *a, **k: _Cl())
    monkeypatch.setattr(merge_runner.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(merge_runner, "monotonic", lambda: ctl.clock)
    asyncio.run(runner._top_book_window(_M(ctl), 0.5))


def _fq(events):
    fqs = [kw for ev, kw in events if ev == "topbook_fillquality"]
    assert len(fqs) == 1, f"expected one fillquality event, got {len(fqs)}"
    return fqs[0]


def test_fully_paired_pair_cost_equals_spent_over_pairs(monkeypatch):
    # Up fills (vanish) + Down completed (FOK) near-end -> all merged, naked 0.
    # When nothing rides naked, every $ spent went into pairs, so pair_cost == spent / pairs.
    ctl = _Ctl()
    runner = _make_runner(ctl)
    events = _capture(monkeypatch)
    _run(ctl, runner, 2, monkeypatch)
    fq = _fq(events)
    assert fq["pairs_merged"] == 5.0
    assert fq["naked_resid"] == 0.0
    assert fq["resid_outcome"] == "flat"
    assert fq["match_naked"] is None
    assert fq["pair_cost"] is not None
    assert 0.0 < fq["pair_cost"] < 1.0
    assert abs(fq["pair_cost"] - fq["spent"] / fq["pairs_merged"]) < 0.005


def test_naked_residual_outcome_and_none_pair_cost(monkeypatch):
    # completion FOK killed -> Down never fills -> naked +5 Up, nothing merged.
    # Up book (0.50/0.99) -> mid 0.745 >= 0.5 -> winner Up -> naked Up == winner -> WON.
    ctl = _Ctl()
    runner = _make_runner(ctl, fok_fills=False)
    events = _capture(monkeypatch)
    _run(ctl, runner, 2, monkeypatch)
    fq = _fq(events)
    assert fq["pairs_merged"] == 0.0
    assert fq["pair_cost"] is None
    assert fq["naked_resid"] == 5.0
    assert fq["match_naked"] == 0.0
    assert fq["resid_outcome"] == "WON"
