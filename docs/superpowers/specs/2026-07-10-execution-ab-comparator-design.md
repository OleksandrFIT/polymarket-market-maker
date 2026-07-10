# Execution A/B Comparator — Design Spec

**Goal:** On the SAME recorded book windows, run both of the bot's execution styles side by side —
`top_book` (maker-first + linked-pair + complete/sell) vs `momentum` (hybrid-taker chase +
never-sell) — and report, per style, the pair economics that decide whether either reaches
0xb27b's (~$0.98 pair, high match:naked): `pair_cost`, `% windows < $1`, `match:naked`, PnL.
A no-risk, offline, reproducible preview to see which execution style targets his economics.

**Status:** New research script `scripts/_exec_ab.py` + one pure helper in `quoter/research/`.
NO change to any production trading code. This is analysis, not a strategy change.

---

## Background & the fidelity asymmetry (the crux — must stay explicit)

Decoded attribution (this session) showed 0xb27b's edge is matched-pair merge (~$0.98/pair) ×
23:1 match:naked × scale, executed as a ~53/47 taker/maker HYBRID. We have two execution styles
in the bot that each replicate PART of that: `top_book` (maker-first, adverse-selected live at
−$9.54) and `momentum` (taker-chase, measured −$0.43…−$0.62/window in v8/v9 sims). The user asked
to compare them head-to-head on identical data.

**The comparison is fundamentally asymmetric in fidelity, and the output MUST say so:**
- **momentum (taker):** we are the aggressor; a fill at the real ask is real → **decision-grade**.
- **top_book (maker):** offline cannot model queue position / adverse selection (the reason we
  built the LIVE `topbook_fillquality` telemetry). The best offline proxy is **shadow-fill**:
  credit a maker fill only when the real tape trades THROUGH our quoted bid in the interval — but
  this is still an **OPTIMISTIC upper bound** (it ignores that a slow MM is selected onto the toxic
  side; the toxicity analysis proved shadow over-states vs live). So the top_book `pair_cost` here
  is BEST-CASE; the momentum `pair_cost` is realistic.

Reading of the result: if even best-case maker fails to reach his pair economics → strong negative
signal. If best-case maker looks good → that is exactly what the LIVE measurement (the separate
gated step, `topbook_fillquality` + `_pairquality.py`) must confirm. **This sim is a cheap preview
of the live measurement, NOT a substitute for it.**

## Non-goals (YAGNI)

- Do NOT change `_top_book_window`, `_momentum_window`, planners, config, or any production path.
- Do NOT claim the top_book side is decision-grade — it is a labeled optimistic shadow-fill.
- No live trading. No new strategy. No auto-tuning.

## Architecture

`scripts/_exec_ab.py` reads a recorded book tape (`data/book_*.jsonl`; each line
`{"slug", "ts", "yes":{bids,asks}, "no":{bids,asks}}`), groups snapshots per window (sorted by
`ts`), resolves the winner via `mm_tape.load_window(slug) -> (tape, winner, open_ts)`, and for
EACH window runs two independent portfolios over the SAME snapshot sequence:

### Unit 1 — top_book (maker, shadow-fill) portfolio per window

Mirrors `_top_book_window`'s ECONOMICS (not its async I/O): each snapshot, quote best_bid+tick on
both sides, cap the light bid by linked-pair (`1 − heavy_avg − margin`); **shadow-fill** = credit
`min(qsize, Σ tape SELL-print size at price ≤ our bid in [ts, next_ts))` on each side (reusing the
exact passive-fill rule already in `scripts/_momentum.py`); accumulate `held_cost`; near-end (last
gate_sec) COMPLETE the light leg (taker at ask) if pair < $1 else SELL the heavy loser; merge
matched pairs each snapshot (accumulate `merged_cost += mq*(avg_up+avg_dn)` — the SAME accounting
the live telemetry uses); residual redeems at resolution. Emit one record (below).

### Unit 2 — momentum (taker) portfolio per window

Mirrors `_momentum_window` economics: append Up-mid to `mid_hist`; `chase_signal(mid_hist, ts,
lookback, threshold)`; on signal, TAKE mover + fader at their real asks (incl. `taker_fee`),
bounded by residual_cap + per_window_cap; never sell; merge each snapshot (same `merged_cost`
accumulation); residual redeems. Emit one record.

### Unit 3 — per-window record (shared shape, matches the live telemetry)

Both units emit a dict with the SAME keys the live `topbook_fillquality` uses so the EXISTING
`quoter/research/pairquality.summarize` consumes them unchanged:
`{style, slug, pair_cost, pairs_merged, naked_resid, resid_outcome, match_naked, completes,
sells, pnl}` (`pnl` added for this sim: `merged + residual_redeem − spent`).

### Unit 4 — pure helper `window_record(...)` in `quoter/research/exec_ab.py`

Extract the record-building math (pair_cost = merged_cost/merged or None; match_naked =
merged/|naked| or None; resid_outcome via winner vs signed naked) into ONE pure, unit-tested
function shared by both portfolios, so the two styles compute the metric identically. The script
imports it; a unit test pins it.

### Output

Run `summarize()` per style, print a side-by-side table: n windows, mean & median `pair_cost`,
`% windows < $1`, median match:naked, PnL/window, outcome split — for `top_book (maker, OPTIMISTIC
shadow-fill)` and `momentum (taker, decision-grade)`, with a one-line header restating the
asymmetry so the numbers are never read as equal-fidelity.

## Data

Recorded tape `data/book_*.jsonl` on the server (~6 days, hundreds of BTC-5m windows). Run on a
slice (server RAM is small — see [[project-poly-quoter-status]]; OOM on full-day). Tape cache
`POLY_MM_CACHE` for `load_window` resolution. Reproducible (same tape → same numbers).

## Testing

- `tests/test_exec_ab.py`: unit-test the pure `window_record` — fully-paired → pair_cost =
  spent-basis/pairs, match_naked None, outcome flat; naked residual → correct signed resid,
  WON/LOST by winner, None pair_cost when nothing merged. (Same invariants as the live telemetry
  test, on the pure helper.)
- Full suite stays green. The script itself is exercised by a smoke run on a tape slice.

## Success criterion

A side-by-side verdict: does either execution style, on identical windows, reach pair ~$0.98 /
high match:naked? Expected from prior work: momentum realistic ≈ v8/v9 (pair near/above $1, −EV);
top_book optimistic-maker likely shows a lower pair_cost BUT that is the best-case that the live
run must verify. Either way the comparison quantifies the maker-vs-taker execution gap on one tape
and tells us whether the live top_book measurement is worth running (it is, unless optimistic maker
already fails).

## Deliverables

- `quoter/research/exec_ab.py` (pure `window_record`) + `tests/test_exec_ab.py`.
- `scripts/_exec_ab.py` (the two-portfolio comparator + side-by-side output).
- No production-code change. Reuses `chase_signal`, `taker_fee`, `pairquality.summarize`,
  `mm_tape.load_window`, and the passive shadow-fill rule from `scripts/_momentum.py`.
