"""Integration tests for the revocable CLOSING gate on the top_book path (chop_gate).

From t>=chop_detect_sec, a committed trend (chop_revoke held chop_confirm_sec) OR the clock
(time_remaining <= freeze_sec) flips the window to CLOSING: accumulation bids are cancelled and
no NEW accumulation quotes are posted, but completion/merge/sell of already-held naked legs still
run. When chop_gate=False the path is byte-identical to the plain top_book loop (no closing,
diff_quotes, all existing top_book tests unchanged).

Reuses the mock harness from tests/test_top_book_complete.py, with a clock-advancing _run so the
CLOSING trend-confirm and freeze triggers can actually fire across ticks.
"""
import asyncio

from quoter.config import Config
from quoter.runner import merge_runner
from quoter.runner.merge_runner import MergeRunner
from quoter.runner.trading_state import TradingState


class _Ctl:
    def __init__(self):
        self.t_remaining = 200.0
        self.tick = 0
        self.clock = 1000.0
        self.dt = 0.0
        self.places = []           # (token, side, price, size, post_only, order_type)
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


def _make_runner(ctl, chop_gate=True, fok_fills=True, per_window_cap=15.0,
                 complete_budget=6.0, chop_detect_sec=30.0, chop_confirm_sec=20.0,
                 freeze_sec=45.0, tb_complete=True):
    r = MergeRunner.__new__(MergeRunner)
    r.cfg = Config(
        strategy="top_book", assets=("BTC",), timeframes=("5m",),
        tb_size=5.0, tb_naked_cap=6.0, tb_tick=0.001, tb_merge_min=5.0,
        per_window_cap=per_window_cap, tb_complete=tb_complete, tb_complete_gate_sec=45.0,
        complete_budget=complete_budget, inv_reconcile_grace_sec=12.0, dry_run=False,
        chop_gate=chop_gate, chop_detect_sec=chop_detect_sec, chop_dev_thresh=0.28,
        chop_lookback_sec=60.0, chop_confirm_sec=chop_confirm_sec,
        replace_shift=0.02, replace_dwell_sec=4.0, freeze_sec=freeze_sec,
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


def _run(ctl, runner, n_ticks, monkeypatch, up=(0.50, 0.99), dn=(0.01, 0.45),
         t_start=200.0, step=15.0):
    ctl.t_remaining = t_start

    class _Cl:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            u = up(ctl.tick) if callable(up) else up
            d = dn(ctl.tick) if callable(dn) else dn
            return _Resp(_book(*u)) if params["token_id"] == "UP" else _Resp(_book(*d))

    async def fake_sleep(_):
        ctl.tick += 1
        ctl.clock += ctl.dt
        ctl.t_remaining = max(1.0, ctl.t_remaining - step)
        if ctl.tick >= n_ticks:
            runner._shutdown = True

    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", lambda *a, **k: _Cl())
    monkeypatch.setattr(merge_runner.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(merge_runner, "monotonic", lambda: ctl.clock)
    asyncio.run(runner._top_book_window(_M(ctl), 0.5))


def _closings(events):
    return [kw for ev, kw in events if ev == "topbook_closing"]


def _acc_posts(ctl):
    # accumulation = resting maker BUYs (post_only, no order_type)
    return [p for p in ctl.places if p[4] and p[1] == "BUY" and p[5] is None]


def _fok_buys(ctl, token):
    return [p for p in ctl.places if p[0] == token and p[1] == "BUY" and p[5] == "FOK"]


def _last_quotes(events):
    qs = [kw for ev, kw in events if ev == "topbook_quotes"]
    return qs[-1] if qs else None


def test_closing_on_late_trend(monkeypatch):
    # Up mid pinned high (0.85, dev 0.35 >= 0.28) with no 0.5-cross -> chop_revoke True; held
    # confirm_sec (>=20) -> CLOSING reason "trend" while still far from freeze (t_rem ~170 > 45).
    ctl = _Ctl()
    runner = _make_runner(ctl)
    events = _capture(monkeypatch)
    _run(ctl, runner, 4, monkeypatch, up=(0.80, 0.90), dn=(0.01, 0.15),
         t_start=200.0, step=15.0)
    cl = _closings(events)
    assert cl, "expected a topbook_closing event"
    assert cl[0]["reason"] == "trend"
    assert _acc_posts(ctl), "accumulation should have posted before closing"
    lq = _last_quotes(events)
    assert lq is not None and lq["up"] is None and lq["dn"] is None, \
        "accumulation must be revoked (no resting quotes) after CLOSING"


def test_closing_by_clock_freeze(monkeypatch):
    # Non-trending mid (0.745, dev 0.245 < 0.28) so ONLY the clock trips: run into freeze_sec.
    ctl = _Ctl()
    runner = _make_runner(ctl)
    events = _capture(monkeypatch)
    _run(ctl, runner, 3, monkeypatch, up=(0.50, 0.99), dn=(0.01, 0.45),
         t_start=60.0, step=20.0)
    cl = _closings(events)
    assert cl, "expected a topbook_closing event"
    assert cl[0]["reason"] == "clock"
    lq = _last_quotes(events)
    assert lq is not None and lq["up"] is None and lq["dn"] is None, \
        "no new accumulation quotes after clock CLOSING"


def test_no_revoke_on_choppy_book(monkeypatch):
    # Up mid oscillates across 0.5 -> chop_revoke stays False; time stays > freeze -> NO closing,
    # accumulation keeps running (Down bid stays resting).
    def up(t):
        return (0.55, 0.65) if t % 2 == 0 else (0.35, 0.45)

    ctl = _Ctl()
    runner = _make_runner(ctl)
    events = _capture(monkeypatch)
    _run(ctl, runner, 4, monkeypatch, up=up, dn=(0.01, 0.15),
         t_start=150.0, step=10.0)
    assert not _closings(events), "choppy book must not trigger CLOSING"
    assert _acc_posts(ctl), "accumulation must keep posting on a choppy book"
    lq = _last_quotes(events)
    assert lq is not None and lq["dn"] is not None, "Down bid should stay resting (not revoked)"


def test_completion_still_fires_in_closing(monkeypatch):
    # tick0 (t_rem 60 > 45): Up maker fills (vanish) -> naked +5 Up. tick1 (t_rem 40 <= 45):
    # clock CLOSING fires AND near-end completion is due -> a Down completion FOK is still placed
    # (CLOSING cancels only resting accumulation, never blocks completion).
    # non-trending book (mid 0.745, dev 0.245 < 0.28) so ONLY the clock trips, not the trend.
    ctl = _Ctl()
    runner = _make_runner(ctl)
    events = _capture(monkeypatch)
    _run(ctl, runner, 3, monkeypatch, up=(0.50, 0.99), dn=(0.01, 0.45),
         t_start=60.0, step=20.0)
    cl = _closings(events)
    assert cl and cl[0]["reason"] == "clock", "expected clock CLOSING"
    assert _fok_buys(ctl, "DN"), "completion FOK must still fire during CLOSING"
    assert runner.state.naked_shares == 0


def test_chop_gate_off_is_unchanged(monkeypatch):
    # chop_gate=False -> plain top_book path: never a topbook_closing, accumulation posts normally.
    ctl = _Ctl()
    runner = _make_runner(ctl, chop_gate=False)
    events = _capture(monkeypatch)
    _run(ctl, runner, 3, monkeypatch, up=(0.80, 0.90), dn=(0.01, 0.15),
         t_start=200.0, step=15.0)
    assert not _closings(events), "chop_gate=False must never CLOSE"
    assert _acc_posts(ctl), "accumulation posts normally on the off path"
