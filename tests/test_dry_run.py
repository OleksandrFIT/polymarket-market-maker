import asyncio
import pytest
from quoter.config import Config


class _SpyClob:
    def __init__(self):
        self.placed = 0
        self.cancelled = 0

    async def place_limit(self, **kw):
        self.placed += 1
        return {"order_id": "real", "status": "live"}

    async def cancel_orders(self, oids):
        self.cancelled += 1

    async def cancel_all(self):
        self.cancelled += 1
        return 1


def _runner(dry):
    from quoter.runner.merge_runner import MergeRunner
    r = MergeRunner.__new__(MergeRunner)          # bypass __init__ (needs creds)
    r.cfg = Config(dry_run=dry)
    r.clob = _SpyClob()
    return r


def test_dry_run_places_nothing():
    r = _runner(True)
    res = asyncio.run(r._place_limit(token_id="t", price=0.5, size=5, side="BUY"))
    assert res == {"order_id": "dryrun", "status": "dry"}
    assert r.clob.placed == 0
    asyncio.run(r._cancel_orders(["x"]))
    assert asyncio.run(r.cancel_all()) == 0
    assert r.clob.cancelled == 0


def test_live_mode_places_real():
    r = _runner(False)
    res = asyncio.run(r._place_limit(token_id="t", price=0.5, size=5, side="BUY"))
    assert res["status"] == "live"
    assert r.clob.placed == 1
    asyncio.run(r.cancel_all())
    assert r.clob.cancelled == 1
