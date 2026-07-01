"""Read-only order-book snapshot collector for the MM queue-fill study. Polls CLOB /book
every 2s for the current BTC 5m window and appends JSONL snapshots. Imports NO order-
placement code and only issues GET requests. Pure helpers (current_slug, snapshot_record)
are unit-tested; the loop is thin I/O verified by running with --once."""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

WINDOW_SEC = 300
CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
UA = {"User-Agent": "Mozilla/5.0 (poly-book)"}


def current_slug(now: float) -> str:
    open_ts = int(now) // WINDOW_SEC * WINDOW_SEC
    return "btc-updown-5m-%d" % open_ts


def _side(book) -> dict:
    b = book or {}
    return {
        "bids": [[float(l["price"]), float(l["size"])] for l in b.get("bids", [])],
        "asks": [[float(l["price"]), float(l["size"])] for l in b.get("asks", [])],
    }


def snapshot_record(ts, slug, yes_book, no_book) -> dict:
    return {"ts": int(ts), "slug": slug, "yes": _side(yes_book), "no": _side(no_book)}
