"""Queue-aware maker-fill measurement from collected order-book snapshots + the trade tape.
Pure `depth_ahead`/`queue_fill`; `load_snapshots` is thin I/O."""
from __future__ import annotations

import json


def depth_ahead(bid_levels: list, price: float) -> float:
    return sum(float(sz) for p, sz in bid_levels if float(p) >= price)


def best_mid(bid_levels: list, ask_levels: list) -> float:
    """Mid from best bid (MAX bid price) and best ask (MIN ask price). CLOB /book returns
    bids ascending and asks descending, so bids[0]/asks[0] are NOT the top of book — always
    reduce by max/min. Falls back to the one available side, or 0.5 if the book is empty."""
    bb = max((float(p) for p, _ in bid_levels), default=None)
    ba = min((float(p) for p, _ in ask_levels), default=None)
    if bb is not None and ba is not None:
        return (bb + ba) / 2
    if bb is not None:
        return bb
    if ba is not None:
        return ba
    return 0.5


def best_ask(ask_levels: list):
    """Best (lowest) ask price. CLOB /book returns asks descending, so reduce by min.
    Returns None if there are no asks."""
    return min((float(p) for p, _ in ask_levels), default=None)


def queue_fill(price: float, size: float, placed_ts: float,
               bid_levels: list, tape: list) -> float:
    ahead = depth_ahead(bid_levels, price)
    consumed = 0.0
    for t in tape:
        if t["ts"] < placed_ts:
            continue
        if t["side"] != "SELL":
            continue
        if t["price"] > price:
            continue
        consumed += float(t["size"])
    return min(float(size), max(0.0, consumed - ahead))


def load_snapshots(path: str, slug: str) -> list:
    """Read a book_YYYYMMDD.jsonl file, return snapshots for one slug, sorted by ts."""
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("slug") == slug:
                out.append(rec)
    out.sort(key=lambda r: r["ts"])
    return out
