# tests/test_fills_feed.py
import asyncio
from quoter.runner.fills_feed import fetch_window_fills


class _FakeResp:
    def __init__(self, data): self._data = data
    def json(self): return self._data
    def raise_for_status(self): pass


class _FakeHttp:
    def __init__(self, data): self._data = data
    async def get(self, url, params=None, headers=None):
        return _FakeResp(self._data)


def test_filters_to_window_and_normalizes_outcome():
    data = [
        {"slug": "btc-updown-5m-1781302200", "side": "BUY", "outcome": "Up", "size": 5, "price": 0.27},
        {"slug": "btc-updown-5m-1781302200", "side": "SELL", "outcome": "Down", "size": 5, "price": 0.18},
        {"slug": "btc-updown-5m-1781301900", "side": "BUY", "outcome": "Up", "size": 5, "price": 0.40},
        {"slug": "eth-updown-5m-1781302200", "side": "BUY", "outcome": "Up", "size": 5, "price": 0.40},
    ]
    fills = asyncio.run(fetch_window_fills("0xFUND", "btc-updown-5m-1781302200", _FakeHttp(data)))
    assert len(fills) == 2
    assert fills[0] == {"side": "YES", "action": "BUY", "size": 5.0, "price": 0.27}
    assert fills[1] == {"side": "NO", "action": "SELL", "size": 5.0, "price": 0.18}


def test_empty_when_no_match():
    http = _FakeHttp([{"slug": "btc-updown-5m-1781301900", "side": "BUY", "outcome": "Up", "size": 5, "price": 0.4}])
    assert asyncio.run(fetch_window_fills("0xFUND", "btc-updown-5m-1781302200", http)) == []
