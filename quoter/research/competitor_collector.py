"""Read-only competitor-activity collector. Polls data-api /activity for a target wallet
every POLL_SEC and appends NEW (deduped) events to data/competitor_<addr8>.jsonl.

The data-api caps at ~3500 recent events; a very active wallet (0xb27b does ~30-40
trades/window) scrolls that in ~20 minutes, so window-level history is only reachable for
a short time after the fact. Polling faster than the scroll-out durably reconstructs the
full per-window history over time — the durable substitute for the un-paginatable old data.
GET-only, imports no order-placement code, never crashes the loop.
Usage: python3 -m quoter.research.competitor_collector [--once] [<address>]"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

ADDR_DEFAULT = "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"   # 0xb27b, the merge-maker
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
API = "https://data-api.polymarket.com/activity"
UA = {"User-Agent": "Mozilla/5.0 (poly-comp)"}
POLL_SEC = 180
MAX_OFFSET = 3000                                            # data-api hard cap ~3500


def event_key(ev: dict) -> tuple:
    """Stable dedup key for an activity event (survives re-fetch of the same event)."""
    return (ev.get("transactionHash"), ev.get("timestamp"), ev.get("type"),
            ev.get("side"), ev.get("size"), ev.get("price"), ev.get("slug"))


def outfile(addr: str) -> str:
    return os.path.join(DATA_DIR, "competitor_%s.jsonl" % addr[2:10])


def _load_seen(path: str) -> set:
    seen: set = set()
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(event_key(json.loads(line)))
            except json.JSONDecodeError:
                pass
    return seen


def _fetch_all(client: httpx.Client, addr: str) -> list:
    out: list = []
    for off in range(0, MAX_OFFSET + 1, 500):
        try:
            r = client.get(API, params={"user": addr, "limit": 500, "offset": off,
                                        "sortBy": "TIMESTAMP", "sortDirection": "DESC"},
                           headers=UA, timeout=15)
            b = r.json()
        except Exception:
            break
        if not isinstance(b, list) or not b:
            break
        out += b
        if len(b) < 500:
            break
    return out


def _tick(client: httpx.Client, addr: str, path: str, seen: set) -> tuple[int, int]:
    events = _fetch_all(client, addr)
    new = 0
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "a") as f:
        for ev in events:
            k = event_key(ev)
            if k in seen:
                continue
            seen.add(k)
            f.write(json.dumps(ev) + "\n")
            new += 1
    return new, len(events)


def main() -> None:
    once = "--once" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    addr = args[0] if args else ADDR_DEFAULT
    path = outfile(addr)
    seen = _load_seen(path)
    with httpx.Client(http2=False) as client:
        while True:
            try:
                new, total = _tick(client, addr, path, seen)
                if once:
                    print("fetched %d reachable, appended %d new -> %s (total seen %d)"
                          % (total, new, path, len(seen)))
                    return
            except Exception as e:                          # never crash the collector
                if once:
                    print("tick error:", e)
                    return
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
