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


def plan_requote(resting: dict, target: list[TBQuote], last_replace: dict, now: float,
                 caps: dict | None = None, replace_shift: float = 0.02, dwell_sec: float = 4.0):
    """Directional, dwell-bounded cancel/replace for ACCUMULATION bids (replaces diff_quotes on the
    top_book accumulation path). A mid move DOWN to our bid is our PLAN (cheap fill on the dump) — do
    not chase down; a mid move UP away makes the bid dead — pulling up trades queue for fill-rate.
    resting: {side:(price,size)}; target: list[TBQuote] (already linked-pair capped); last_replace:
    {side: ts}; caps: {side: current linked-pair ceiling} (or None).
    Returns (cancel, post, cap_sides).
    Rules/side, in order:
      1. no resting -> post (new side).
      2. CAP-OVERRIDE (invariant, NOT queue-opt): if resting price > caps[side], cancel+repost down
         to the (capped) target, IGNORING direction and dwell — a resting bid above the current cap
         would assemble a pair >= $1 if filled (the cap tightens as heavy_avg grows, i.e. in trends).
         -> cap_sides.
      3. pull UP: target >= resting + shift AND dwell elapsed -> cancel+repost (shift-driven).
      4. else (down move / within shift / dwell not elapsed) -> keep.
    Sides absent from target -> cancel."""
    tgt = {q.side: q for q in target}
    cancel, post, cap_sides = [], [], []
    for q in target:
        if q.side not in resting:
            post.append(q)
            continue
        rp = resting[q.side][0]
        cap = caps.get(q.side) if caps else None
        if cap is not None and rp > cap + 1e-12:                 # 2. cap-override (invariant)
            cancel.append(q.side)
            post.append(q)
            cap_sides.append(q.side)
            continue
        if q.price >= rp + replace_shift and (now - last_replace.get(q.side, -1e18)) >= dwell_sec:
            cancel.append(q.side)
            post.append(q)                                       # 3. pull up (shift-driven)
    for s in resting:
        if s not in tgt:
            cancel.append(s)
    return cancel, post, cap_sides


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


def taker_fee(price: float, rate: float = 0.07) -> float:
    """Polymarket crypto TAKER fee per share = `rate * min(price, 1-price)` (crypto_fees_v2,
    exponent 1). VERIFIED LIVE 2026-07-11 from the market object: rate=0.07 (was mis-modelled at
    0.018 → under-counted the fee ~4x). Peaks at 0.50 (~3.5c/share) and falls to ~0 at the 0/1
    extremes. Makers pay 0 (feeSchedule.takerOnly) and never call this. See [[reference-polymarket-fees]]."""
    p = min(max(price, 0.0), 1.0)
    return rate * min(p, 1.0 - p)


def maker_rebate(price: float, rebate_rate: float = 0.2, taker_rate: float = 0.07) -> float:
    """Maker rebate per share earned when OUR resting order is filled: `rebate_rate * taker_fee`.
    Polymarket crypto_fees_v2: makers pay 0 and receive rebate_rate (0.2) of the counterparty's
    taker fee, per fill, roughly linear in our own filled volume. VERIFIED LIVE 2026-07-11
    (feeSchedule.rebateRate=0.2). This is the maker path's SECOND revenue stream — it shifts the
    break-even pair cost above $1.00 (~$1.01 near mid). Liquidity-rewards are a SEPARATE program,
    inactive here (rewards.rates=None, min_size 50 > our size 5)."""
    return rebate_rate * taker_fee(price, taker_rate)


_REGIME_GRID = list(range(0, 301, 20))


def resample_grid(pts: list[tuple[float, float]]) -> list[float] | None:
    """Resample an irregular (rel_ts, value) path onto the fixed 0..300s/20s grid via linear
    interpolation (flat-hold outside the endpoints). Returns None if fewer than 3 points — the
    same guard the behavior baseline uses. Identical to _chop_detector_sim.resample."""
    if len(pts) < 3:
        return None
    out, j = [], 0
    for g in _REGIME_GRID:
        while j + 1 < len(pts) and pts[j + 1][0] <= g:
            j += 1
        if g <= pts[0][0]:
            out.append(pts[0][1])
        elif g >= pts[-1][0]:
            out.append(pts[-1][1])
        else:
            (t0, m0), (t1, m1) = pts[j], pts[min(j + 1, len(pts) - 1)]
            out.append(m0 if t1 == t0 else m0 + (m1 - m0) * (g - t0) / (t1 - t0))
    return out


def classify_regime(series: list[float]) -> str:
    """Sign-cross taxonomy on a mid series (>=0.5 vs <0.5): >=2 crosses -> 'chop', 1 -> 'reversal',
    0 -> 'trend'. Identical to _chop_detector_sim.regime — the SAME fn behind the 51/28/20 baseline."""
    sgn = [1 if x >= 0.5 else -1 for x in series]
    crosses = sum(1 for i in range(len(sgn) - 1) if sgn[i] != sgn[i + 1])
    return "chop" if crosses >= 2 else ("reversal" if crosses == 1 else "trend")


def window_regime(mid_hist: list[tuple[float, float]]) -> str | None:
    """Post-hoc regime label for a window's full causal Up-mid path (rel_ts, up_mid). Resamples to
    the baseline grid then classifies. Returns None when the path is too short to classify (<3 pts)."""
    u = resample_grid(sorted(mid_hist))
    return classify_regime(u) if u is not None else None


def chop_revoke(mid_hist: list[tuple[float, float]], now: float,
                dev_thresh: float = 0.28, lookback_sec: float = 60.0) -> bool:
    """Sliding, causal trend-commit signal for the revocable CLOSING gate. mid_hist: list of
    (rel_ts, up_mid) in time order. Returns True iff the window is committing to a trend at `now`:
      |mid(now) - 0.5| >= dev_thresh  AND  no 0.5-crossing in the trailing [now - lookback_sec, now].
    Cumulative history (crossed-ever) is deliberately NOT used: a mid-trend that oscillated early then
    commits late must revoke; a 'crossed ever' stamp would wrongly keep it chop forever."""
    if not mid_hist:
        return False
    cur = mid_hist[-1][1]
    if abs(cur - 0.5) < dev_thresh:
        return False
    tail = [(t, m) for (t, m) in mid_hist if t >= now - lookback_sec]
    for i in range(len(tail) - 1):
        if (tail[i][1] >= 0.5) != (tail[i + 1][1] >= 0.5):
            return False                       # crossed 0.5 in the tail -> still oscillating
    return True
