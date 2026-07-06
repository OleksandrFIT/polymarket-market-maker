"""Integration test: linked-pair quoting inside the real _top_book_window loop.

Proves the async-fill fix works end-to-end: after a leg fills (heavy Up), and the market MOVES
(Down best-bid jumps to 0.74), the light Down bid is posted at the LINKED cap (1-up_avg-margin
= 0.669), NOT best+tick (0.741) — so any pairing fill stays < $1 instead of 1.06.
"""
import asyncio

from quoter.config import Config
from quoter.runner import merge_runner
from quoter.runner.merge_runner import MergeRunner
from quoter.runner.trading_state import TradingState


class _Ctl:
    def __init__(self):
        self.tick = 0
        self.places = []          # (token, price, post_only)
        self.open = set()
        self.seq = 0


class _Resp:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d


def _book(bid, ask=0.99):
    return {"bids": [{"price": f"{bid:.3f}", "size": "500"}],
            "asks": [{"price": f"{ask:.3f}", "size": "500"}]}


class _M:
    slug = "btc-updown-5m-linked"
    yes_token = "UP"
    no_token = "DN"
    open_ts = 1

    def time_remaining(self):
        return 200.0             # mid-window (no near-end completion/SELL)


def _make_runner(ctl):
    r = MergeRunner.__new__(MergeRunner)
    r.cfg = Config(
        strategy="top_book", assets=("BTC",), timeframes=("5m",),
        tb_size=5.0, tb_naked_cap=6.0, tb_tick=0.001, tb_merge_min=5.0,
        per_window_cap=15.0, tb_link_margin=0.01, tb_complete=False, dry_run=False,
    )
    r.state = TradingState()
    r._shutdown = False
    r._traded_windows = set()

    async def fake_place(**kw):
        ctl.seq += 1
        oid = f"o{ctl.seq}"
        ctl.places.append((kw["token_id"], round(kw["price"], 3), kw.get("post_only")))
        # Up maker fills instantly (never in open -> vanishes -> credited); Down maker rests
        if kw.get("post_only") and kw["token_id"] == "DN":
            ctl.open.add(oid)
        return {"order_id": oid, "status": "live"}

    async def fake_cancel(oids):
        for o in oids:
            ctl.open.discard(o)

    async def fake_cancel_all():
        return 0

    r._place_limit = fake_place
    r._cancel_orders = fake_cancel
    r._open_order_ids = lambda: set(ctl.open)
    r._order_matched = lambda oid: 5.0
    r._merge_pairs = lambda m, q: _true()
    r.cancel_all = fake_cancel_all
    return r


async def _true():
    return True


def test_linked_pair_caps_light_bid_after_async_move(monkeypatch):
    ctl = _Ctl()
    runner = _make_runner(ctl)

    class _Cl:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            if params["token_id"] == "UP":
                return _Resp(_book(0.32))                 # Up bid 0.32 -> our Up quote 0.321, fills
            # Down: tick 1 near 0.48, then the market MOVES to 0.74 from tick 2 on
            return _Resp(_book(0.48 if ctl.tick == 0 else 0.74))

    async def fake_sleep(_):
        ctl.tick += 1
        if ctl.tick >= 2:
            runner._shutdown = True

    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", lambda *a, **k: _Cl())
    monkeypatch.setattr(merge_runner.asyncio, "sleep", fake_sleep)
    asyncio.run(runner._top_book_window(_M(), 0.5))

    # after Up filled @0.321 (avg 0.321) and Down's book jumped to 0.74, the Down bid must be
    # posted at the LINKED cap 1-0.321-0.01 = 0.669, NOT best+tick 0.741.
    dn = [p for (tok, p, po) in ctl.places if tok == "DN"]
    assert 0.669 in dn, f"Down not capped to 0.669; Down posts={dn}"
    assert 0.741 not in dn, f"Down posted at un-capped 0.741 (async pair >$1): {dn}"
