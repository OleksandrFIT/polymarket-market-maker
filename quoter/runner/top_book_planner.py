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


def skew_ok(inv_side: float, inv_other: float, size: float, naked_cap: float) -> bool:
    """HARD skew cap: may we rest a size-`size` order on this side?

    The order we'd rest can itself fill fully before the next requote re-evaluates, so
    count it: worst-case naked = inv_side + size - inv_other. True iff that stays ≤ cap
    (boundary-inclusive — the cap is reachable, filling to EXACTLY cap, but not
    breachable). The old gate looked only at already-filled inv and let a burst-fill
    overshoot by up to `size` (seen live: cap 10 → 14/20 naked in a trending window).

    Used by ``plan_top_book`` AND re-checked in the runner's post loop: the planner gate
    runs once per tick on tick-top inv, but a fill can be credited MID-tick via the
    cancel-and-reprice path, so the post loop must re-check against the fresh inv (mirrors
    the ``committed_gate`` re-check already done there for capital).
    """
    return inv_side + size - inv_other <= naked_cap


def plan_top_book(yes_book, no_book, inv_up: float, inv_dn: float,
                  naked_cap: float, size: float, tick: float = 0.001) -> list[TBQuote]:
    out: list[TBQuote] = []
    inv = {"Up": inv_up, "Down": inv_dn}
    for side, book in (("Up", yes_book), ("Down", no_book)):
        other = "Down" if side == "Up" else "Up"
        if not skew_ok(inv[side], inv[other], size, naked_cap):
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


def link_pair_bids(target: list[TBQuote], inv: dict, avg: dict, margin: float) -> list[TBQuote]:
    """LINKED-PAIR quoting: cap each LIGHT-side (under-weight) bid so a fill pairs against the
    held HEAVY leg for < $1 by construction — bid <= 1 - heavy_avg - margin.

    Fixes the async-fill loss: quoting both sides at best+tick independently fills the two legs
    at DIFFERENT market states (market moves between fills), so pair cost can exceed $1 (live-
    proven: leg1 at 0.32, market moves, leg2 at 0.74 -> pair 1.06). Live book shows best_bid_up
    + best_bid_dn = 0.99 (a real +1c merge edge) — but only if BOTH legs fill at the same state.
    Capping the light bid at 1 - heavy_avg - margin guarantees any pairing fill stays < $1; if
    the market moved away the capped bid rests below best and simply doesn't fill (-> hold naked,
    handled near-end), never overpaying. Heavy side is left unchanged. A light cap <= 0 drops
    that side (can't pair profitably). margin <= 0 -> unchanged (feature off).
    """
    if margin <= 0:
        return target
    # Per-order safety: a light order is priced against the heavy avg AT POST TIME. If more
    # heavy fills later (raising heavy_avg) before this light order fills, the resting order
    # isn't re-tightened until the next requote — but it still pairs < $1 against the heavy
    # shares that existed when it was priced (the new heavy shares pair against FUTURE light
    # fills). So each light fill is individually capped; the blended pair cost stays < $1.
    out: list[TBQuote] = []
    for q in target:
        heavy = "Down" if q.side == "Up" else "Up"
        if inv.get(heavy, 0.0) > inv.get(q.side, 0.0) and avg.get(heavy) is not None:
            capped = min(q.price, round(1.0 - avg[heavy] - margin, 3))
            if capped <= 0:
                continue
            out.append(TBQuote(q.side, capped, q.size))
        else:
            out.append(q)
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
