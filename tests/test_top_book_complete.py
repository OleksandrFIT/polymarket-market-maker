"""Integration tests for top_book near-end pair completion — incl. the two bugs an
adversarial review caught in the first cut:

  1. a KILLED FOK still returns success+order_id; crediting `qty` on id-presence alone
     phantom-closes a naked leg that actually rides to resolution (invisible loss).
  2. a CUMULATIVE tb_completed counter permanently suppresses genuinely-new naked after
     the first completion+merge (balance_complete_qty(5, 5)=0).

Drives one real `_top_book_window`: Up fills and builds a naked leg; near window-end the
loop taker-buys (FOK) the light leg, credits ONLY the real matched size, and merges.
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
        self.clock = 1000.0            # patched monotonic() (advance to age completes)
        self.dt = 0.0                  # clock advance per tick
        self.places = []               # (token, price, size, post_only, order_type)
        self.open = set()
        self.kind = {}                 # oid -> "FOK" | "maker"
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
    slug = "btc-updown-5m-complete"
    yes_token = "UP"
    no_token = "DN"
    open_ts = 1

    def __init__(self, ctl):
        self._ctl = ctl

    def time_remaining(self):
        return self._ctl.t_remaining


def _make_runner(ctl, fok_matched=5.0):
    r = MergeRunner.__new__(MergeRunner)
    r.cfg = Config(
        strategy="top_book", assets=("BTC",), timeframes=("5m",),
        tb_size=5.0, tb_naked_cap=6.0, tb_tick=0.001, tb_merge_min=5.0,
        per_window_cap=15.0, tb_complete=True, tb_complete_gate_sec=45.0,
        inv_reconcile_grace_sec=12.0, dry_run=False,
    )
    r.state = TradingState()
    r._shutdown = False
    r._traded_windows = set()

    async def fake_place(**kw):
        ctl.seq += 1
        oid = f"o{ctl.seq}"
        is_fok = kw.get("order_type") == "FOK"
        ctl.kind[oid] = "FOK" if is_fok else "maker"
        ctl.places.append((kw["token_id"], kw["price"], kw["size"],
                           kw.get("post_only"), kw.get("order_type")))
        # Up maker -> instantly filled (never in open -> vanishes -> credited); Down maker
        # -> stays open (no maker fill); FOK -> not tracked in open.
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
        # FOK completion returns fok_matched (0 = killed); maker Up fill returns full size.
        return fok_matched if ctl.kind.get(oid) == "FOK" else 5.0

    r._place_limit = fake_place
    r._cancel_orders = fake_cancel
    r._open_order_ids = lambda: set(ctl.open)
    r._order_matched = order_matched
    r._merge_pairs = fake_merge
    r.cancel_all = fake_cancel_all
    return r


def _run(ctl, runner, n_ticks, monkeypatch):
    class _Cl:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            if params["token_id"] == "UP":
                return _Resp(_book(0.50, 0.99))      # Up bid fills our maker; ask far
            return _Resp(_book(0.01, 0.45))          # Down: no maker fill; cheap ask to complete

    async def fake_sleep(_):
        ctl.tick += 1
        ctl.clock += ctl.dt
        ctl.t_remaining = 40.0                        # in the completion gate from tick 2 on
        if ctl.tick >= n_ticks:
            runner._shutdown = True

    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", lambda *a, **k: _Cl())
    monkeypatch.setattr(merge_runner.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(merge_runner, "monotonic", lambda: ctl.clock)
    asyncio.run(runner._top_book_window(_M(ctl), 0.5))


def _fok_places(ctl):
    return [p for p in ctl.places if p[0] == "DN" and p[3] is False and p[4] == "FOK"]


def test_near_end_completes_naked_pair_to_zero(monkeypatch):
    ctl = _Ctl()
    runner = _make_runner(ctl, fok_matched=5.0)
    _run(ctl, runner, n_ticks=2, monkeypatch=monkeypatch)
    fok = _fok_places(ctl)
    assert fok and fok[0][2] == 5.0, ctl.places       # FOK BUY 5 Down to complete
    assert runner.state.naked_shares == 0             # completed pair merged -> zero naked


def test_killed_fok_does_not_phantom_close(monkeypatch):
    # FOK is KILLED (matched 0): the bot must NOT phantom-credit/merge — it must still
    # KNOW it holds the naked leg (state reflects reality), not falsely read flat.
    ctl = _Ctl()
    runner = _make_runner(ctl, fok_matched=0.0)
    _run(ctl, runner, n_ticks=2, monkeypatch=monkeypatch)
    assert _fok_places(ctl), "completion should still attempt the FOK"
    assert runner.state.naked_shares == 5             # naked NOT phantom-closed


def test_completion_not_suppressed_after_merge(monkeypatch):
    # cycle 1 completes+merges a naked; clock advances past the feed-lag grace (12s);
    # cycle 2's genuinely-new naked must complete AGAIN (a cumulative counter would
    # suppress it: balance_complete_qty(5, 5)=0).
    ctl = _Ctl()
    ctl.dt = 20.0                                     # age each completion past the 12s grace
    runner = _make_runner(ctl, fok_matched=5.0)
    _run(ctl, runner, n_ticks=3, monkeypatch=monkeypatch)
    assert len(_fok_places(ctl)) >= 2, ctl.places     # second naked NOT suppressed
    assert runner.state.naked_shares == 0
