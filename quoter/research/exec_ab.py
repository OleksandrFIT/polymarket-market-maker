"""Pure execution-A/B: run the bot's two execution styles over one window's book snapshots and
return a fillquality-shaped record for each. top_book = maker + shadow-fill (OPTIMISTIC — offline
cannot model queue/adverse-selection); momentum = taker-chase (decision-grade, we are aggressor).
window_record computes the shared pair-cost/outcome metric identically for both styles."""
from quoter.research.chase import chase_signal

FEE_RATE = 0.018
TICK = 0.001


def fee(p):
    return FEE_RATE * min(p, 1.0 - p)


def _mid(book):
    bb = max((float(p) for p, _ in book["bids"]), default=None)
    ba = min((float(p) for p, _ in book["asks"]), default=None)
    if bb is None and ba is None:
        return None
    return ba if bb is None else (bb if ba is None else (bb + ba) / 2)


def _ask(book):
    a = book["asks"]
    if not a:
        return None, 0.0
    p, sz = min(((float(p), float(s)) for p, s in a), key=lambda x: x[0])
    return p, sz


def window_record(style, slug, merged, merged_cost, inv_up, inv_dn, winner, spent,
                  completes=0, sells=0):
    """Fillquality-shaped record (shared by both styles). pnl = merged pairs redeem $1 each +
    winning residual redeemed - net spent (loser residual expires; a sell reduces `spent`)."""
    naked = inv_up - inv_dn
    resid_side = "Up" if naked > 0 else ("Down" if naked < 0 else None)
    resid_outcome = ("flat" if resid_side is None
                     else "WON" if resid_side == winner else "LOST")
    redeem = abs(naked) if resid_side == winner else 0.0
    return {
        "style": style, "slug": slug,
        "pair_cost": round(merged_cost / merged, 4) if merged > 0 else None,
        "pairs_merged": merged,
        "naked_resid": round(naked, 1),
        "resid_outcome": resid_outcome,
        "match_naked": round(merged / abs(naked), 1) if abs(naked) >= 1e-9 else None,
        "completes": completes, "sells": sells,
        "spent": round(spent, 2),
        "pnl": round(merged + redeem - spent, 2),
    }


def _merge(inv, held, merged, merged_cost):
    mq = min(inv["Up"], inv["Down"])
    if mq > 0:
        avg_up = held["Up"] / inv["Up"] if inv["Up"] > 0 else 0.0
        avg_dn = held["Down"] / inv["Down"] if inv["Down"] > 0 else 0.0
        merged_cost += mq * (avg_up + avg_dn)
        for s in ("Up", "Down"):
            a = held[s] / inv[s] if inv[s] > 0 else 0.0
            held[s] = max(0.0, held[s] - mq * a)
            inv[s] -= mq
        merged += mq
    return merged, merged_cost


def top_book_window(snaps, tape, winner, slug, cap=6.0, size=5.0, link_margin=0.01,
                    gate_sec=45.0, pwc=15.0, complete_budget=6.0):
    """Maker best+tick both sides, shadow-fill vs SELL prints <= our bid, linked-pair cap on the
    light side, near-end complete (<$1) or sell (>=$1) the naked leg, merge each snapshot."""
    st = {0: [t for t in tape if t["oi"] == 0 and t["side"] == "SELL"],
          1: [t for t in tape if t["oi"] == 1 and t["side"] == "SELL"]}
    oi = {"Up": 0, "Down": 1}
    inv = {"Up": 0.0, "Down": 0.0}
    held = {"Up": 0.0, "Down": 0.0}
    spent = merged = merged_cost = 0.0
    completes = sells = 0
    open_ts = int(slug.rsplit("-", 1)[1])
    budget = pwc + complete_budget
    for i, snap in enumerate(snaps):
        ts = snap["ts"]
        end = snaps[i + 1]["ts"] if i + 1 < len(snaps) else ts + 2
        book = {"Up": snap["yes"], "Down": snap["no"]}
        avg = {s: (held[s] / inv[s] if inv[s] > 0 else None) for s in ("Up", "Down")}
        for side in ("Up", "Down"):
            other = "Down" if side == "Up" else "Up"
            b = book[side]
            if not b["bids"] or inv[side] - inv[other] >= cap:
                continue
            bb = max(float(p) for p, _ in b["bids"])
            ba, _sz = _ask(b)
            our = round(bb + TICK, 3)
            if inv[other] > inv[side] and avg[other] is not None:       # linked-pair light cap
                our = min(our, round(1.0 - avg[other] - link_margin, 3))
            if ba is None or our >= ba or our >= 0.99 or our <= 0:
                continue
            v = sum(t["size"] for t in st[oi[side]] if ts <= t["ts"] < end and t["price"] <= our)
            f = min(size, v)
            if f > 0 and spent + f * our <= budget:
                inv[side] += f
                held[side] += f * our
                spent += f * our
        near_end = (open_ts + 300 - ts) <= gate_sec
        naked = inv["Up"] - inv["Down"]
        if near_end and abs(naked) >= 1:
            heavy = "Up" if naked > 0 else "Down"
            light = "Down" if heavy == "Up" else "Up"
            heavy_avg = held[heavy] / inv[heavy] if inv[heavy] > 0 else 0.0
            lap, lsz = _ask(book[light])
            if lap is not None and lap < 0.99 and heavy_avg + lap < 1.0:
                f = min(abs(naked), lsz)
                if f > 0 and spent + f * lap <= budget:
                    inv[light] += f
                    held[light] += f * lap
                    spent += f * lap
                    completes += 1
            else:
                hb = max((float(p) for p, _ in book[heavy]["bids"]), default=None)
                if hb is not None and hb > 0:
                    f = float(int(abs(naked)))
                    if f > 0:
                        avg_h = held[heavy] / inv[heavy] if inv[heavy] > 0 else 0.0
                        held[heavy] = max(0.0, held[heavy] - f * avg_h)
                        inv[heavy] -= f
                        spent -= f * hb            # sell returns cash
                        sells += 1
        merged, merged_cost = _merge(inv, held, merged, merged_cost)
    return window_record("top_book", slug, merged, merged_cost, inv["Up"], inv["Down"],
                         winner, spent, completes, sells)


def momentum_window(snaps, tape, winner, slug, size=5.0, lookback=30, threshold=0.03,
                    resid_cap=8.0, pwc=15.0):
    """Taker-chase: on a momentum signal, take mover + fader at their real asks (incl. taker fee),
    never sell, merge each snapshot. pair_cost uses raw prices; the fee shows up in spent/pnl."""
    inv = {"Up": 0.0, "Down": 0.0}
    held = {"Up": 0.0, "Down": 0.0}
    spent = merged = merged_cost = 0.0
    mid_hist = []
    for snap in snaps:
        ts = snap["ts"]
        book = {"Up": snap["yes"], "Down": snap["no"]}
        m = _mid(snap["yes"])
        if m is not None:
            mid_hist.append((ts, m))
        sig = chase_signal(mid_hist, ts, lookback, threshold)
        if sig is not None:
            fade = "Down" if sig == "Up" else "Up"
            for side in (sig, fade):
                other = "Down" if side == "Up" else "Up"
                if inv[side] - inv[other] >= resid_cap:
                    continue
                ap, asz = _ask(book[side])
                if ap is None or ap >= 0.99 or ap <= 0:
                    continue
                f = min(size, asz)
                if f <= 0:
                    continue
                unit = f * (ap + fee(ap))
                if spent + unit > pwc:
                    continue
                inv[side] += f
                held[side] += f * ap             # raw price basis for pair_cost; fee is in spent
                spent += unit
        merged, merged_cost = _merge(inv, held, merged, merged_cost)
    return window_record("momentum", slug, merged, merged_cost, inv["Up"], inv["Down"],
                         winner, spent)


def hybrid_window(snaps, tape, winner, slug, cap=6.0, size=5.0, lookback=30, threshold=0.03,
                  resid_cap=8.0, pwc=15.0):
    """0xb27b's ~53/47 replica: MAKER bids on both sides catch the cheap FALLER (shadow-fill vs
    SELL prints <= our bid) + TAKER chases the rising WINNER EVERY tick from early (average in at a
    low basis), merge continuously, never sell. Residual leans the winner (fair coin)."""
    st = {0: [t for t in tape if t["oi"] == 0 and t["side"] == "SELL"],
          1: [t for t in tape if t["oi"] == 1 and t["side"] == "SELL"]}
    oi = {"Up": 0, "Down": 1}
    inv = {"Up": 0.0, "Down": 0.0}
    held = {"Up": 0.0, "Down": 0.0}
    spent = merged = merged_cost = 0.0
    mid_hist = []
    for i, snap in enumerate(snaps):
        ts = snap["ts"]
        end = snaps[i + 1]["ts"] if i + 1 < len(snaps) else ts + 2
        book = {"Up": snap["yes"], "Down": snap["no"]}
        m = _mid(snap["yes"])
        if m is not None:
            mid_hist.append((ts, m))
        # MAKER: rest best+tick both sides, shadow-fill vs SELL prints <= our bid (cheap faller)
        for side in ("Up", "Down"):
            other = "Down" if side == "Up" else "Up"
            b = book[side]
            if not b["bids"] or inv[side] - inv[other] >= cap:
                continue
            bb = max(float(p) for p, _ in b["bids"])
            ba, _sz = _ask(b)
            our = round(bb + TICK, 3)
            if ba is None or our >= ba or our >= 0.99 or our <= 0:
                continue
            v = sum(t["size"] for t in st[oi[side]] if ts <= t["ts"] < end and t["price"] <= our)
            f = min(size, v)
            if f > 0 and spent + f * our <= pwc:
                inv[side] += f
                held[side] += f * our
                spent += f * our
        # TAKER: chase the rising winner EVERY tick (continuous early averaging-in)
        sig = chase_signal(mid_hist, ts, lookback, threshold)
        if sig is not None:
            other = "Down" if sig == "Up" else "Up"
            if inv[sig] - inv[other] < resid_cap:
                ap, asz = _ask(book[sig])
                if ap is not None and 0 < ap < 0.99:
                    f = min(size, asz)
                    unit = f * (ap + fee(ap))
                    if f > 0 and spent + unit <= pwc:
                        inv[sig] += f
                        held[sig] += f * ap
                        spent += unit
        merged, merged_cost = _merge(inv, held, merged, merged_cost)
    return window_record("hybrid", slug, merged, merged_cost, inv["Up"], inv["Down"],
                         winner, spent)
