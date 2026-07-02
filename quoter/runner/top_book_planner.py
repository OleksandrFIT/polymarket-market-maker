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
    bids = (book or {}).get("bids") or []
    return max((float(lvl["price"]) for lvl in bids), default=None)


def _best_ask(book) -> float | None:
    asks = (book or {}).get("asks") or []
    return min((float(lvl["price"]) for lvl in asks), default=None)


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
    # Pair-cost sanity gate: books come from two sequential GETs; a move between them
    # can leave our_up + our_dn > 1.0 (matched pair merges for exactly $1 -> guaranteed
    # loss). Keep only the cheaper side; the pricier quote is the stale/risky one.
    if len(out) == 2 and out[0].price + out[1].price > 0.999:
        out = [min(out, key=lambda q: q.price)]
    return out


def diff_quotes(current: dict, target: list[TBQuote]):
    """current: {side: (price, size)} of what we have resting.
    Returns (sides_to_cancel, quotes_to_post). A resting quote is kept iff its side is in
    target at the SAME PRICE — size is intentionally ignored: partial fills keep queue
    priority (cancelling a partially-filled order to repost full size would forfeit it).
    No size top-up at the same price."""
    tgt = {q.side: q for q in target}
    cancel = [s for s, (p, _sz) in current.items()
              if s not in tgt or tgt[s].price != p]
    post = [q for q in target
            if q.side not in current or current[q.side][0] != q.price]
    return cancel, post


def plan_merge(inv_up: float, inv_dn: float, merge_min: float) -> float:
    m = min(inv_up, inv_dn)
    return m if m >= merge_min else 0.0


def committed_gate(cost_up: float, cost_dn: float, resting: dict,
                   quote: TBQuote, cap: float) -> bool:
    """Committed-capital gate: may `quote` be posted without breaching `cap`?

    committed = realized cost basis (NEVER decremented — not even after a merge,
    so worst-case gross spend stays bounded if merge fails) + notional of orders
    still resting ({side: (price, size)}). True iff committed + quote notional
    stays strictly below cap (exactly-at-cap is blocked — conservative for live
    money; a cancelled-then-repriced side is not in `resting`, so a reprice at
    unchanged notional passes).
    """
    committed = cost_up + cost_dn + sum(p * sz for (p, sz) in resting.values())
    return committed + quote.price * quote.size < cap
