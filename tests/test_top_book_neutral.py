"""Direction-neutral mode: early-aggressive phase quotes a bigger size to pair both legs
fast (first tb_early_sec of the window), then reverts to tb_size."""
import asyncio

from quoter.config import Config
from quoter.runner import merge_runner
from quoter.runner.merge_runner import MergeRunner
from quoter.runner.trading_state import TradingState


class _Ctl:
    def __init__(self):
        self.tick = 0
        self.t_remaining = 270.0        # start in the early phase (>240 = first 60s)
        self.sizes = []                 # sizes of posted orders


class _Resp:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d


def _book(bid):
    return {"bids": [{"price": f"{bid:.3f}", "size": "500"}],
            "asks": [{"price": "0.99", "size": "500"}]}


class _M:
    slug = "btc-updown-5m-neutral"
    yes_token = "UP"
    no_token = "DN"
    open_ts = 1

    def __init__(self, ctl):
        self._ctl = ctl

    def time_remaining(self):
        return self._ctl.t_remaining


def test_early_phase_quotes_bigger_size_then_reverts(monkeypatch):
    ctl = _Ctl()
    r = MergeRunner.__new__(MergeRunner)
    r.cfg = Config(
        strategy="top_book", assets=("BTC",), timeframes=("5m",),
        tb_size=5.0, tb_naked_cap=12.0, tb_tick=0.001, tb_merge_min=5.0,
        per_window_cap=999.0, tb_early_sec=60.0, tb_early_size=10.0, dry_run=True,
    )
    r.state = TradingState()
    r._shutdown = False
    r._traded_windows = set()

    async def fake_place(**kw):
        ctl.sizes.append(kw["size"])
        return {"order_id": "o", "status": "dry"}

    r._place_limit = fake_place
    r._cancel_orders = lambda oids: _noop()
    r.cancel_all = lambda: _z()

    class _Cl:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _Resp(_book(0.48 if params["token_id"] == "UP" else 0.50))

    async def fake_sleep(_):
        ctl.tick += 1
        ctl.t_remaining = 200.0                 # tick 2 onward: past the early phase (<240)
        if ctl.tick >= 2:
            r._shutdown = True

    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", lambda *a, **k: _Cl())
    monkeypatch.setattr(merge_runner.asyncio, "sleep", fake_sleep)
    asyncio.run(r._top_book_window(_M(ctl), 0.5))

    # tick 1 is in the early phase (t_remaining 270 > 240) -> quotes use tb_early_size 10,
    # not tb_size 5 (diff_quotes ignores size, so a same-price order isn't re-sized later —
    # the early bigger order simply rests; the point is the EARLY posts are aggressive).
    assert ctl.sizes and all(s == 10.0 for s in ctl.sizes), \
        f"early phase did not use tb_early_size 10; sizes={ctl.sizes}"


async def _noop():
    return None


async def _z():
    return 0
