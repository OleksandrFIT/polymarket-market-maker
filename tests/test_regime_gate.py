import asyncio

from quoter.config import Config
from quoter.runner import merge_runner
from quoter.runner.merge_runner import MergeRunner
from quoter.runner.regime_gate import regime_tradeable

MAX = 25.0


def _runner():
    r = MergeRunner.__new__(MergeRunner)                 # bypass __init__ (needs creds)
    r.cfg = Config(strategy="top_book", regime_max_move_usd=25.0, regime_lookback_min=5)
    return r


def _fake_client(rows=None, boom=False):
    class _Resp:
        def json(self):
            return rows

    class _Cl:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            if boom:
                raise RuntimeError("binance down")
            return _Resp()

    return lambda *a, **k: _Cl()


def test_regime_method_skips_trend(monkeypatch):
    rows = [[0, 0, 0, 0, str(62700 + i * 20)] for i in range(5)]   # net +80 -> trend
    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", _fake_client(rows))
    assert asyncio.run(_runner()._regime_tradeable()) is False


def test_regime_method_trades_calm(monkeypatch):
    rows = [[0, 0, 0, 0, str(62700 + (i % 2) * 5)] for i in range(5)]  # net 0 -> calm
    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", _fake_client(rows))
    assert asyncio.run(_runner()._regime_tradeable()) is True


def test_regime_method_fails_open_on_error(monkeypatch):
    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", _fake_client(boom=True))
    assert asyncio.run(_runner()._regime_tradeable()) is True         # fail-open


def test_calm_small_net_move_is_tradeable():
    assert regime_tradeable([62700, 62705, 62698, 62710], MAX) is True   # net +10 < 25


def test_uptrend_large_net_move_skipped():
    assert regime_tradeable([62700, 62720, 62740, 62760], MAX) is False  # net +60


def test_downtrend_large_net_move_skipped():
    assert regime_tradeable([62760, 62740, 62710, 62700], MAX) is False  # net -60


def test_chop_big_swings_small_net_is_tradeable():
    # whipsaw that returns near start: NET move tiny -> pairs still fill both sides -> trade
    assert regime_tradeable([62700, 62760, 62690, 62705], MAX) is True   # net +5


def test_boundary_exactly_max_is_trend():
    assert regime_tradeable([62700, 62725], MAX) is False                # 25 not < 25


def test_just_under_boundary_is_calm():
    assert regime_tradeable([62700, 62724], MAX) is True                 # 24 < 25


def test_insufficient_data_fails_open():
    assert regime_tradeable([], MAX) is True
    assert regime_tradeable([62700], MAX) is True
