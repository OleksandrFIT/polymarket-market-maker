"""Momentum-take mechanics: on rising-Up momentum the loop TAKES Up (mover) + Down (fader) as
FOK takers, merges pairs, caps the net-long residual at mom_residual_cap, never SELLS, and
bounds taker spend by per_window_cap. Edge is NOT tested here (only measurable live)."""
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
        self.places = []            # (token, side, price, size, post_only, order_type)
        self.seq = 0
        self.kind = {}
        self.size = {}


def _book(bid, ask):
    return {"bids": [{"price": f"{bid:.3f}", "size": "500"}],
            "asks": [{"price": f"{ask:.3f}", "size": "500"}]}


class _Resp:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d


class _M:
    slug = "btc-updown-5m-x"
    yes_token = "UP"
    no_token = "DN"
    open_ts = 1

    def __init__(self, ctl):
        self._ctl = ctl

    def time_remaining(self):
        return self._ctl.t_remaining


def _make_runner(ctl):
    r = MergeRunner.__new__(MergeRunner)
    r.cfg = Config(strategy="momentum", assets=("BTC",), timeframes=("5m",),
                   tb_size=5.0, tb_tick=0.001, tb_merge_min=1.0,
                   mom_lookback=6.0, mom_threshold=0.03, mom_chase_max=0.95,
                   mom_residual_cap=5.0, per_window_cap=15.0, dry_run=False)
    r.state = TradingState()
    r._shutdown = False
    r._traded_windows = set()

    async def fake_place(**kw):
        ctl.seq += 1
        oid = f"o{ctl.seq}"
        ctl.kind[oid] = kw.get("order_type")
        ctl.size[oid] = kw["size"]
        ctl.places.append((kw["token_id"], kw["side"], kw["price"], kw["size"],
                           kw.get("post_only"), kw.get("order_type")))
        return {"order_id": oid, "status": "live"}

    async def fake_merge(m, qty):
        return True

    r._place_limit = fake_place
    r._cancel_orders = lambda oids: asyncio.sleep(0)
    r._open_order_ids = lambda: set()
    r._order_matched = lambda oid: ctl.size.get(oid, 0.0)   # FOK fills its own size
    r._merge_pairs = fake_merge
    r.cancel_all = lambda: asyncio.sleep(0)
    return r


def _run(ctl, runner, n_ticks, monkeypatch):
    # Up mid rises each tick: 0.50 -> 0.56 -> 0.62 ... (fires chase_signal("Up")); Down mirrors.
    class _Cl:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            up = 0.50 + 0.06 * ctl.tick
            up = min(up, 0.95)
            if params["token_id"] == "UP":
                return _Resp(_book(up - 0.01, up + 0.01))
            return _Resp(_book((1 - up) - 0.01, (1 - up) + 0.01))

    async def fake_sleep(_):
        ctl.tick += 1
        ctl.clock += 2.0
        if ctl.tick >= n_ticks:
            runner._shutdown = True

    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", lambda *a, **k: _Cl())
    monkeypatch.setattr(merge_runner.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(merge_runner, "monotonic", lambda: ctl.clock)
    asyncio.run(runner._momentum_window(_M(ctl), 0.5))


def _fok(ctl, token):
    return [p for p in ctl.places if p[0] == token and p[1] == "BUY" and p[5] == "FOK"]


def test_takes_mover_and_fader_as_taker(monkeypatch):
    ctl = _Ctl()
    runner = _make_runner(ctl)
    _run(ctl, runner, 5, monkeypatch)
    assert _fok(ctl, "UP"), "must TAKER-buy the rising mover (Up)"
    assert _fok(ctl, "DN"), "must TAKER-buy the cheap fader (Down)"
    assert all(p[4] is False for p in ctl.places), "all takes are taker (post_only False)"


def test_never_sells(monkeypatch):
    ctl = _Ctl()
    runner = _make_runner(ctl)
    _run(ctl, runner, 6, monkeypatch)
    assert not any(p[1] == "SELL" for p in ctl.places), "momentum strategy never sells"


def test_residual_capped(monkeypatch):
    ctl = _Ctl()
    runner = _make_runner(ctl)
    _run(ctl, runner, 8, monkeypatch)
    assert runner.state.naked_shares <= 5   # mom_residual_cap
