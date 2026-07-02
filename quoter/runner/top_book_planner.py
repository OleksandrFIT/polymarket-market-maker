"""Pure top-of-book MM planner (phase-27). Bid best+tick on BOTH sides (price improvement
-> alone at our level, queue ahead = 0), skew-cap naked inventory, never cross, never sell.
Validated offline: +1.1% edge / 68% win / 97% matched on 197 real windows."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TBQuote:
    side: str      # "Up" | "Down"
    price: float
    size: float


def _best_bid(book) -> float | None:
    bids = (book or {}).get("bids", [])
    return max((float(l["price"]) for l in bids), default=None)


def _best_ask(book) -> float | None:
    asks = (book or {}).get("asks", [])
    return min((float(l["price"]) for l in asks), default=None)


def plan_top_book(yes_book, no_book, inv_up: float, inv_dn: float,
                  naked_cap: float, size: float, tick: float = 0.001) -> list[TBQuote]:
    out: list[TBQuote] = []
    inv = {"Up": inv_up, "Down": inv_dn}
    for side, book in (("Up", yes_book), ("Down", no_book)):
        other = "Down" if side == "Up" else "Up"
        if inv[side] - inv[other] >= naked_cap:
            continue
        bb = _best_bid(book)
        if bb is None:
            continue
        our = round(bb + tick, 3)
        ba = _best_ask(book)
        if ba is not None and our >= ba:
            continue
        if our >= 0.99:
            continue
        out.append(TBQuote(side, our, float(size)))
    return out


def diff_quotes(current: dict, target: list[TBQuote]):
    """current: {side: (price, size)} of what we have resting.
    Returns (sides_to_cancel, quotes_to_post). Unchanged quotes are kept (queue priority)."""
    tgt = {q.side: q for q in target}
    cancel = [s for s, (p, sz) in current.items()
              if s not in tgt or (tgt[s].price, tgt[s].size) != (p, sz)]
    post = [q for q in target
            if q.side not in current or (current[q.side][0], current[q.side][1]) != (q.price, q.size)]
    return cancel, post


def plan_merge(inv_up: float, inv_dn: float, merge_min: float) -> float:
    m = min(inv_up, inv_dn)
    return m if m >= merge_min else 0.0
