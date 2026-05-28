"""Load resolved markets from state.db and fetch their CLOB price series.

Price series are cached to quoter/backtest/data/<market_id>.json so we hit
the API only once per market.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request

from quoter.backtest.models import MarketWindow, PricePoint

_CLOB = "https://clob.polymarket.com/prices-history"
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def load_markets_from_db(db_path: str = "state.db") -> list[MarketWindow]:
    """All RESOLVED markets with a known winning side and yes_token."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT market_id, asset, timeframe, open_ts, expire_ts, "
        "winning_side, yes_token FROM markets "
        "WHERE status='RESOLVED' AND winning_side IS NOT NULL "
        "AND yes_token IS NOT NULL"
    ).fetchall()
    con.close()
    return [
        MarketWindow(
            market_id=r["market_id"], asset=r["asset"],
            timeframe=r["timeframe"], open_ts=int(r["open_ts"]),
            expire_ts=int(r["expire_ts"]), winning_side=r["winning_side"],
            yes_token=r["yes_token"],
        )
        for r in rows
    ]


def parse_price_history(raw: dict) -> list[PricePoint]:
    """Convert CLOB prices-history JSON to PricePoint list."""
    return [PricePoint(int(h["t"]), float(h["p"])) for h in raw.get("history", [])]


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def fetch_price_series(window: MarketWindow, *, use_cache: bool = True) -> list[PricePoint]:
    """Fetch (or load cached) YES-token price series for one market window."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    cache = os.path.join(_DATA_DIR, f"{window.market_id}.json")
    if use_cache and os.path.exists(cache):
        with open(cache) as f:
            return parse_price_history(json.load(f))
    url = (
        f"{_CLOB}?market={window.yes_token}"
        f"&startTs={window.open_ts - 60}&endTs={window.expire_ts + 60}&fidelity=1"
    )
    raw = _http_get_json(url)
    with open(cache, "w") as f:
        json.dump(raw, f)
    return parse_price_history(raw)
