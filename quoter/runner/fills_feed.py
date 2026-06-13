# quoter/runner/fills_feed.py
"""Fetch our REAL fills for one window from the Polymarket data-api activity feed.

Replaces the live loop's "order vanished ⇒ filled" guess. The data-api returns
our trades keyed by funder (proxy) address; we filter to the exact window slug and
normalize outcome→side. Shape verified live 2026-06-13:
  {"slug": str, "side": "BUY"|"SELL", "outcome": "Up"|"Down", "size": num, "price": num}
"""

from __future__ import annotations

ACTIVITY_URL = "https://data-api.polymarket.com/activity"
_UA = {"User-Agent": "Mozilla/5.0"}


async def fetch_window_fills(funder: str, slug: str, http) -> list[dict]:
    """Return normalized fills for `slug`:
    {"side": "YES"|"NO", "action": "BUY"|"SELL", "size": float, "price": float}.
    `http` is an httpx.AsyncClient (or compatible) with `.get`."""
    resp = await http.get(
        ACTIVITY_URL,
        params={"user": funder, "type": "TRADE", "limit": 500},
        headers=_UA,
    )
    resp.raise_for_status()
    out: list[dict] = []
    for t in resp.json():
        if str(t.get("slug", "")) != slug:
            continue
        out.append({
            "side": "YES" if t.get("outcome") == "Up" else "NO",
            "action": t.get("side"),
            "size": float(t.get("size", 0)),
            "price": float(t.get("price", 0)),
        })
    return out
