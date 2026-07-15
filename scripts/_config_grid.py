"""OFFLINE threshold grid-search for the chop-gated maker-both tactic. Runs a GATED top_book
shadow sim (maker best+tick both sides, shadow-fill vs SELL prints <= our bid, revocable CLOSING
gate mirroring production _top_book_window) over recorded BTC 5m book tapes and ranks the grid

    replace_shift  in {0.01, 0.02, 0.03}
  x freeze_sec     in {30, 45, 60}
  x chop_lookback  in {40, 60, 80}                                 = 27 configs

by shadow pair-cost (rebate-adjusted) and total PnL. Output feeds the final production config.

CAVEAT: the shadow-fill (fill whenever a SELL print crosses our bid, no queue-position model) is
OPTIMISTIC in ABSOLUTE terms (real maker fills are worse: queue + adverse selection). It ranks the
configs' RELATIVE order adequately, which is all the grid needs.

Memory-safe: parse each book file ONCE into compressed top-of-book snaps, run all 27 configs on the
cached snaps, then free the file before the next (book files 110-140MB, 1.9GB box).

Run: POLY_MM_CACHE=... .venv/bin/python scripts/_config_grid.py <book_jsonl> [more...]
"""
import sys
import json
import collections
import statistics as st

from quoter.research.mm_tape import load_window
from quoter.research.exec_ab import _ask, _mid, _merge, window_record, TICK
from quoter.runner.top_book_planner import chop_revoke, maker_rebate, window_regime


def top_book_window_gated(snaps, tape, winner, slug, *,
                          replace_shift=0.02, chop_dev_thresh=0.28, chop_lookback_sec=60.0,
                          chop_detect_sec=100.0, chop_confirm_sec=10.0, freeze_sec=45.0,
                          cap=6.0, size=5.0, link_margin=0.01, pwc=15.0, complete_budget=6.0,
                          revoke_mode="hard", hard_cap=False):
    """Gated maker-both shadow: a copy of exec_ab.top_book_window's body plus the revocable CLOSING
    gate (mirrors production merge_runner._top_book_window). While NOT closing, accumulate maker
    best+tick both sides (shadow-fill vs SELL prints <= our bid, linked-pair light cap). A committed
    trend (chop_revoke held chop_confirm_sec, from t>=chop_detect_sec) OR the clock
    (freeze_sec left) flips the window to CLOSING; once closing, NEW accumulation stops but the
    near-end complete(<$1)/sell(>=$1) of already-held naked legs and the per-snap merge keep running.

    revoke_mode: on a TREND close (mid-window), "hard" = cancel all resting bids immediately
    (completion-only after) — the production default; "soft" = keep the pre-close resting bids alive
    (post no new legs, no pull-up, no cap-retighten) so a REVERT of the lean fills them cheap, and
    cancel only at clock-freeze. Soft makes a false-trend-revoke on a chop window nearly free (the
    revert refills the standing bids) while still protecting against a real trend (no new legs, cap
    holds). Clock closes are identical under both modes (they happen at freeze anyway).

    Returns (rec, rebate): `rec` = window_record(style="top_book_gated", ...) with two extra keys —
    `rec["closing_reason"]` ("trend"|"clock"|None, the CAUSAL decision) and `rec["hindsight"]` (the
    post-hoc regime via the shared window_regime); `rebate` = summed maker_rebate over all maker fills
    (rebate-adjusted pair cost = pair_cost - rebate/merged).

    NOTE: `replace_shift` is accepted but currently UNUSED. In a shadow that fills whenever a SELL
    print crosses the bid there is no queue-position model, so requote *timing* (shift/dwell) has
    almost no effect and this axis is expected to be near-flat here; it is swept for completeness and
    decided LIVE. The kwarg keeps the grid dimension real and the fn ready when a queue model lands.
    """
    st_sell = {0: [t for t in tape if t["oi"] == 0 and t["side"] == "SELL"],
               1: [t for t in tape if t["oi"] == 1 and t["side"] == "SELL"]}
    oi = {"Up": 0, "Down": 1}
    inv = {"Up": 0.0, "Down": 0.0}
    held = {"Up": 0.0, "Down": 0.0}
    spent = merged = merged_cost = 0.0
    completes = sells = 0
    rebate = 0.0
    open_ts = int(slug.rsplit("-", 1)[1])
    budget = pwc + complete_budget
    mid_hist = []                              # (rel_ts, up_mid) causal path for the CLOSING gate
    closing = False
    closing_reason = None                      # "trend" | "clock" — the causal decision
    trend_since = None
    frozen = {"Up": None, "Down": None}        # last resting bid/side (kept alive under SOFT revoke)
    naked_at_freeze = None                      # abs naked captured at FIRST closing (freeze) tick
    max_pair_cost = 0.0                         # max observed per-merge pair cost (linked-pair invariant)
    for i, snap in enumerate(snaps):
        ts = snap["ts"]
        end = snaps[i + 1]["ts"] if i + 1 < len(snaps) else ts + 2
        book = {"Up": snap["yes"], "Down": snap["no"]}
        up_mid = _mid(book["Up"])
        if up_mid is not None:
            mid_hist.append((ts - open_ts, up_mid))
        elapsed = ts - open_ts
        # REVOCABLE CLOSING trigger (mirrors production _top_book_window): from t>=chop_detect_sec a
        # committed trend (chop_revoke held chop_confirm_sec) OR the clock (freeze_sec left) closes.
        if not closing:
            clock = (open_ts + 300 - ts) <= freeze_sec
            trend = (elapsed >= chop_detect_sec
                     and chop_revoke(mid_hist, elapsed, chop_dev_thresh, chop_lookback_sec))
            trend_since = (trend_since if (trend and trend_since is not None)
                           else (elapsed if trend else None))
            trend_confirmed = (trend and trend_since is not None
                               and (elapsed - trend_since) >= chop_confirm_sec)
            if clock or trend_confirmed:
                closing = True
                closing_reason = "trend" if trend_confirmed else "clock"
                if naked_at_freeze is None:      # abs naked at the first tick the window goes closing
                    naked_at_freeze = abs(inv["Up"] - inv["Down"])
        avg = {s: (held[s] / inv[s] if inv[s] > 0 else None) for s in ("Up", "Down")}
        near_end = (open_ts + 300 - ts) <= freeze_sec
        # ACCUMULATION (maker best+tick both sides) runs while NOT closing; it also records the last
        # resting bid/side in `frozen`. SOFT revoke: after a TREND close, keep filling against those
        # frozen bids (no recompute/pull-up/new leg) until clock-freeze, so a revert of the lean
        # refills the standing bids. HARD revoke: no fills once closing (bids cancelled).
        if not closing:
            # PASS 1: compute each eligible side's linked-pair-capped bid (mirrors production
            # link_pair_bids TWO-SIDED cap: bind against the OTHER side's avg whenever it holds
            # inventory, not only the lighter side).
            bids = {}
            for side in ("Up", "Down"):
                other = "Down" if side == "Up" else "Up"
                b = book[side]
                # hard_cap mirrors the PRODUCTION skew_ok (inv+size-inv_other <= cap, counts the
                # in-flight order) so post-fill naked never exceeds cap; the default (looser, pre-fill
                # >=cap) can overshoot to ~cap+size and OVERSTATES the naked drag / tail.
                skewed = ((inv[side] + size - inv[other] > cap) if hard_cap
                          else (inv[side] - inv[other] >= cap))
                if not b["bids"] or skewed:
                    continue
                bb = max(float(p) for p, _ in b["bids"])
                ba, _sz = _ask(b)
                our = round(bb + TICK, 3)
                if avg[other] is not None:                                  # two-sided linked-pair cap
                    our = min(our, round(1.0 - avg[other] - link_margin, 3))
                if ba is None or our >= ba or our >= 0.99 or our <= 0:
                    continue
                bids[side] = our
            # JOINT-SUM cap: from an (near-)empty book neither avg exists, so two simultaneous fills
            # could assemble a pair >= $1 — cap the SUM of the two bids at 1 - link_margin.
            if len(bids) == 2 and bids["Up"] + bids["Down"] > 1.0 - link_margin:
                cut = round((bids["Up"] + bids["Down"] - (1.0 - link_margin)) / 2.0, 3)
                bids = {s: round(p - cut, 3) for s, p in bids.items()}
                bids = {s: p for s, p in bids.items() if p > 0}
            # PASS 2: shadow-fill against the capped bids.
            for side, our in bids.items():
                frozen[side] = our                          # last resting bid (kept alive under SOFT)
                v = sum(t["size"] for t in st_sell[oi[side]]
                        if ts <= t["ts"] < end and t["price"] <= our)
                f = min(size, v)
                if f > 0 and spent + f * our <= budget:
                    inv[side] += f
                    held[side] += f * our
                    spent += f * our
                    rebate += maker_rebate(our) * f          # maker rebate revenue on this fill
        elif revoke_mode == "soft" and not near_end:
            # SOFT: fill ONLY against the frozen pre-close bids (no recompute, no pull-up, no new leg).
            for side in ("Up", "Down"):
                other = "Down" if side == "Up" else "Up"
                our = frozen[side]
                if our is None or inv[side] - inv[other] >= cap:
                    continue
                v = sum(t["size"] for t in st_sell[oi[side]]
                        if ts <= t["ts"] < end and t["price"] <= our)
                f = min(size, v)
                if f > 0 and spent + f * our <= budget:
                    inv[side] += f
                    held[side] += f * our
                    spent += f * our
                    rebate += maker_rebate(our) * f
        # near-end completion/sell of an already-held naked leg is NOT gated (runs even when closing)
        naked = inv["Up"] - inv["Down"]
        if near_end and abs(naked) >= 1:
            heavy = "Up" if naked > 0 else "Down"
            light = "Down" if heavy == "Up" else "Up"
            heavy_avg = held[heavy] / inv[heavy] if inv[heavy] > 0 else 0.0
            lap, lsz = _ask(book[light])
            if lap is not None and lap < 0.99 and heavy_avg + lap < 1.0 - link_margin:
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
        if min(inv["Up"], inv["Down"]) > 0:              # observe the pair cost about to be merged
            au = held["Up"] / inv["Up"]
            ad = held["Down"] / inv["Down"]
            max_pair_cost = max(max_pair_cost, au + ad)
        merged, merged_cost = _merge(inv, held, merged, merged_cost)
    if naked_at_freeze is None:                           # window ended before ever going closing
        naked_at_freeze = abs(inv["Up"] - inv["Down"])
    rec = window_record("top_book_gated", slug, merged, merged_cost, inv["Up"], inv["Down"],
                        winner, spent, completes, sells)
    rec["closing_reason"] = closing_reason           # causal decision ("trend"|"clock"|None)
    rec["hindsight"] = window_regime(mid_hist)        # post-hoc regime (shared baseline classifier)
    rec["naked_at_freeze"] = naked_at_freeze          # abs naked at freeze (or final if no freeze)
    rec["max_pair_cost"] = max_pair_cost              # max per-merge pair cost (invariant: < $1)
    # exit_branch by PRIORITY RULE. NOTE: the sim does NOT model FOK-kill — it fills the near-end
    # completion/sell whenever the price condition holds; a "rode_*" branch therefore means the
    # near-end price CONDITION was never met (no bid to sell into / pair>=$1 with no completable light
    # leg / budget exhausted), NOT a FOK that was tried and killed. Live FOK-kill is a separate,
    # unmodelled tail (see report).
    final_naked = abs(inv["Up"] - inv["Down"])
    if naked_at_freeze < 1:
        rec["exit_branch"] = "clean"
    elif final_naked < 1:
        rec["exit_branch"] = "completed" if completes > 0 else "sold"
    else:
        rec["exit_branch"] = "rode_won" if rec["resid_outcome"] == "WON" else "rode_lost"
    return rec, rebate


# ---- streaming book-file parser (adapted from _chop_detector_sim.process_file) -------------------

def _tob(book):
    bb = max((float(p) for p, _ in book.get("bids", [])), default=None)
    ba = min((float(p) for p, _ in book.get("asks", [])), default=None)
    return bb, ba


def parse_file(path):
    """Stream one book file -> {slug: sorted compressed top-of-book snaps}. Parse ONCE (expensive);
    the caller runs all 27 configs on the cached snaps then frees them."""
    snaps = collections.defaultdict(list)
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        sl = r.get("slug", "")
        if not sl.startswith("btc-updown-5m-"):
            continue
        ybb, yba = _tob(r.get("yes", {}))
        nbb, nba = _tob(r.get("no", {}))
        snaps[sl].append({"ts": r["ts"],
                          "yes": {"bids": [[ybb, "500"]] if ybb is not None else [],
                                  "asks": [[yba, "500"]] if yba is not None else []},
                          "no": {"bids": [[nbb, "500"]] if nbb is not None else [],
                                 "asks": [[nba, "500"]] if nba is not None else []}})
    for sn in snaps.values():
        sn.sort(key=lambda x: x["ts"])
    return snaps


GRID_SHIFT = [0.01, 0.02, 0.03]
GRID_FREEZE = [30.0, 45.0, 60.0]
GRID_LOOKBACK = [40.0, 60.0, 80.0]


def main():
    configs = [(s, f, lb) for s in GRID_SHIFT for f in GRID_FREEZE for lb in GRID_LOOKBACK]
    assert (0.02, 45.0, 60.0) in configs, "defaults 0.02/45/60 must be inside the grid"
    # per-config accumulators: keyed by (shift, freeze, lookback)
    pair = collections.defaultdict(list)       # rebate-unadjusted pair_cost (skip None)
    pair_eff = collections.defaultdict(list)    # pair_cost - rebate/merged (merged>0)
    pnl = collections.defaultdict(list)

    n_windows = 0
    for path in sys.argv[1:]:
        snaps_by_slug = parse_file(path)
        for slug, sn in snaps_by_slug.items():
            w = load_window(slug)
            if not w or not w[0]:
                continue
            tape, winner, _ = w
            n_windows += 1
            for (shift, freeze, lookback) in configs:
                rec, rebate = top_book_window_gated(
                    sn, tape, winner, slug,
                    replace_shift=shift, freeze_sec=freeze, chop_lookback_sec=lookback)
                key = (shift, freeze, lookback)
                if rec["pair_cost"] is not None:
                    pair[key].append(rec["pair_cost"])
                m = rec["pairs_merged"]
                if m > 0:
                    pair_eff[key].append(rec["pair_cost"] - rebate / m)
                pnl[key].append(rec["pnl"])
        snaps_by_slug = None                    # free the file's snaps before the next
        print("  ...%s -> %d windows so far" % (path.split("/")[-1], n_windows), file=sys.stderr)

    print("\n=== chop-gate threshold grid (%d windows) ===" % n_windows)
    print("SHADOW-OPTIMISM CAVEAT: shadow-fill is optimistic in ABSOLUTE terms; ranking is relative.")
    print("replace_shift is near-FLAT in shadow (no queue model) and is decided live.\n")
    print("%-22s %5s %10s %12s %12s %10s"
          % ("config(shift/frz/lb)", "n", "pair_cost", "pair_eff", "pnl/window", "tot_pnl"))
    rows = []
    for (shift, freeze, lookback) in configs:
        key = (shift, freeze, lookback)
        p = pair[key]
        pe = pair_eff[key]
        pl = pnl[key]
        m_pair = st.mean(p) if p else float("nan")
        m_eff = st.mean(pe) if pe else float("nan")
        m_pnl = st.mean(pl) if pl else 0.0
        t_pnl = sum(pl)
        rows.append((key, len(pl), m_pair, m_eff, m_pnl, t_pnl))
        print("%-22s %5d %10.4f %12.4f %12.4f %+10.1f"
              % ("%.2f/%.0f/%.0f" % (shift, freeze, lookback), len(pl),
                 m_pair, m_eff, m_pnl, t_pnl))

    # TOP 5 ranked by mean pair_cost_effective ASC (cheapest pair wins), tie-break total pnl DESC.
    ranked = sorted(rows, key=lambda r: (r[3], -r[5]))
    print("\n=== TOP 5 by rebate-adjusted pair_cost (asc), tie-break total pnl (desc) ===")
    for key, n, m_pair, m_eff, m_pnl, t_pnl in ranked[:5]:
        print("  shift %.2f  freeze %2.0f  lookback %2.0f  |  pair_eff %.4f  pair %.4f  tot_pnl %+.1f"
              % (key[0], key[1], key[2], m_eff, m_pair, t_pnl))


if __name__ == "__main__":
    main()
