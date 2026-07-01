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


_token_cache: dict[str, tuple[str, str]] = {}


def _resolve_tokens(client: httpx.Client, slug: str):
    """Return (yes_token, no_token) for a slug via gamma, cached. None on failure."""
    if slug in _token_cache:
        return _token_cache[slug]
    try:
        r = client.get("%s/markets" % GAMMA, params={"slug": slug}, headers=UA, timeout=10)
        arr = r.json()
        toks = json.loads(arr[0]["clobTokenIds"]) if arr else None
        if toks and len(toks) == 2:
            _token_cache[slug] = (toks[0], toks[1])
            return _token_cache[slug]
    except Exception:
        pass
    return None


def _get_book(client: httpx.Client, token_id: str) -> dict:
    r = client.get("%s/book" % CLOB, params={"token_id": token_id}, headers=UA, timeout=10)
    return r.json()


def _tick(client: httpx.Client) -> dict | None:
    """One collection tick: resolve tokens, fetch both books, return the JSONL record or None."""
    now = time.time()
    slug = current_slug(now)
    toks = _resolve_tokens(client, slug)
    if not toks:
        return None
    yb = _get_book(client, toks[0])
    nb = _get_book(client, toks[1])
    return snapshot_record(now, slug, yb, nb)


def _append(rec: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    day = time.strftime("%Y%m%d", time.gmtime(rec["ts"]))
    with open(os.path.join(DATA_DIR, "book_%s.jsonl" % day), "a") as f:
        f.write(json.dumps(rec) + "\n")


def main() -> None:
    once = "--once" in sys.argv
    with httpx.Client(http2=False) as client:
        while True:
            try:
                rec = _tick(client)
                if rec is not None:
                    _append(rec)
                    if once:
                        print("wrote snapshot for", rec["slug"],
                              "yes_bids", len(rec["yes"]["bids"]),
                              "no_bids", len(rec["no"]["bids"]))
            except Exception as e:  # never crash the service
                if once:
                    print("tick error:", e)
            if once:
                return
            time.sleep(2)


if __name__ == "__main__":
    main()
