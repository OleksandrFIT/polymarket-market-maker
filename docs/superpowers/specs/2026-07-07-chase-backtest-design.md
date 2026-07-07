# Momentum-Chase Backtest — Design Spec

**Goal:** Determine, offline on real order-book data, whether adding a causal momentum
*chase* of the winning side to our passive top-of-book maker improves risk-adjusted PnL —
by flipping the end-of-window residual from the LOSER (adverse) to the WINNER (redeemable)
— versus the passive baseline.

**Status:** Phase 2 of the "trade like 0xb27b" effort. Phase 1 (collect book data) is DONE
(6 days already on the server). Phase 3 (live) is a separate, gated spec written only if
this backtest shows a real improvement.

---

## Background / grounding (measured, not assumed)

Real-book baseline via `scripts/_top_book.py` on 74 resolved windows (2026-07-04 slice):

| metric | value |
|---|---|
| passive top-of-book edge | **+0.68%** (win 62%, matched 97%) |
| theta sensitivity | +0.68% @ θ=1.0, holds to θ=0.25, dies at θ=0.10 |
| **fee sensitivity** | +0.28% @ 0.2c/sh, **−0.30% @ 0.5c/sh** — edge dies past ~0.5c |
| regime split | seg0 +1.27% (win 71%) / seg1 −0.44% (win 46%) |

So the passive base is mildly POSITIVE (this corrects an earlier tape-only proxy that
wrongly read −1.97% — that proxy bid at `mid`, not real `best_bid+tick`, so its pair_cost
was artefactually >$1). The tape-only mm_sim harness is NOT trustworthy for pair economics;
this spec uses the real-book `_top_book.py` harness.

Why chase might help: a passive bid absorbs the FALLING (losing) side, so the residual it
holds to resolution tends to be the loser (adverse). 0xb27b's data shows he TAKES the
rising (winning) side as it runs and completes the cheap loser — his residual is the WINNER,
which he redeems for $1. Chase = a causal momentum tilt (aligns with the proven momentum
edge memory) grafted onto the pairing/merge engine.

## Non-goals (YAGNI)

- No live-bot changes. This is a read-only research script only.
- No changes to `plan_top_book`, `merge_runner`, or any order-placement path.
- No new data collection beyond the running `book_collector` (data already sufficient).

## Data

- Input: `data/book_YYYYMMDD.jsonl` (2s snapshots, full depth both sides), already on server.
- Use OLD days (>1 day old) so gamma resolution is FINAL. Recent-window winners MISLABEL
  (2026-07-07: gamma said a window's winner=Up but it redeemed $0 = actually lost).
- Winner + taker tape via `load_window(slug)` (cached to disk via `POLY_MM_CACHE`).

## Hard constraint: memory (server is 1.9 GB RAM)

`_top_book.py` loads the whole book file into RAM; a full day (120 MB, ~288 windows, ~89
book levels/side) peaks at ~1.4 GB → **OOM-killed**. Requirements:
- Run on SLICES (~70–90 windows, ~10k snapshots, ~30 MB → ~200 MB RAM), OR
- Add a streaming top-of-book trim (each snapshot → best bid/ask only; depth is UNUSED —
  the harness reads only `max(bids)`/`min(asks)`), shrinking a day ~20× so it fits.
- The tape cache MUST be disk-backed: `POLY_MM_CACHE=/home/ubuntu/cache_poly_mm` (the
  default `/tmp/poly_mm_cache` is tmpfs and eats ~760 MB of RAM). `mm_tape.CACHE` is now
  env-overridable for this.
- Run backtests as a `systemd-run` unit (survives SSH); poll journal/output file.

## Architecture

Extend the validated `scripts/_top_book.py` (do NOT fork the fill model — reuse it) with a
chase layer. Three units with clear boundaries:

### Unit 1 — `chase_signal(mid_hist, lookback_sec, threshold) -> "Up" | "Down" | None` (pure)
- Input: list of `(ts, up_mid)` seen SO FAR (causal — never future ticks), current ts.
- Returns the side whose mid rose by ≥ `threshold` over the last `lookback_sec`, else None.
- Pure and unit-tested (rising Up → "Up"; flat → None; rising Down/falling Up → "Down").

### Unit 2 — chase execution (inside the per-snapshot loop of the backtest `run()`)
- After the existing passive maker fills + merge, if `chase_signal` names a side AND we are
  under-weight on it (`inv[side] <= inv[other]`):
  - TAKE that side at its current `best_ask`, size `CHASE_SIZE`, subject to:
    - price ceiling `CHASE_MAX` (default 0.85; sweep 0.80/0.85/0.90) — never chase above it,
    - the same per-window committed-capital cap as the maker path.
  - Credit inv/spent exactly like a maker fill (it pays the ask, not the bid).
- Passive maker bids on both sides stay unchanged (still fill the cheap side to complete).
- Merge `min(Up,Down)` each tick (existing); residual redeems (winner $1 / loser $0).

### Unit 3 — sweep + metrics (extend the `run()` reporting)
- Report per config: **edge%**, win%, matched%, **pair_cost**, **adverse** (loser shares held
  to resolution — the headline), `$/win`.
- Print PASSIVE vs CHASE side-by-side. Sweep grid:
  - lookback_sec ∈ {20, 40, 60}
  - threshold ∈ {0.03, 0.05, 0.08}
  - CHASE_MAX ∈ {0.80, 0.85, 0.90}
  - CHASE_SIZE ∈ {5} (fixed first pass)
  - re-run under fee ∈ {0, 0.002, 0.005} (edge dies past ~0.5c — decisive gate).

## What the backtest must expose (the real risk)

**Whipsaw:** chase Up on false momentum → Up reverses → we hold expensive Up that loses. The
adverse metric + per-segment/day breakdown must show how often momentum is false and its net
cost. If chase raises adverse or fails out-of-segment, it is rejected.

## Success criteria (decision rule)

Chase is worth taking to Phase 3 (live) ONLY if, across ≥3 separate OLD days spanning regimes:
1. chase edge > passive edge net of a realistic fee (assume ≥0.2c/share until real fee known),
2. chase `adverse` (loser residual) is LOWER than passive, and
3. no single day/segment flips chase materially negative (robustness, not one lucky regime).

If chase fails 1–3, the recommendation is to run the PASSIVE gated default (already +0.68%),
and the "trade every window like 0xb27b" idea is closed as not replicable maker-side.

## Testing

- `chase_signal` gets pytest unit tests (rising/flat/falling, boundary at threshold, causal —
  ignores future ticks).
- The backtest script itself is read-only research; validated by reproducing the passive
  baseline (+0.68% on the 74-window slice) before the chase layer is added.

## Deliverables

- `scripts/_chase.py` (or extend `_top_book.py`) with Units 1–3.
- `tests/test_chase_signal.py`.
- A results summary (passive vs chase across ≥3 days) written back here or to a results note.

---

## Results (2026-07-07) — CHASE REJECTED

Ran `scripts/_chase.py` on ~74-window slices of three separate resolved days.

| Day | passive edge (fee 0) | best chase edge (fee 0) | passive @0.2c | chase @0.2c | passive adverse | chase adverse |
|---|---|---|---|---|---|---|
| Jul 2 | **+0.90%** | +0.40% | **+0.50%** | +0.01% | 0.9 | 0.5 |
| Jul 3 | **+1.19%** | +0.08% | **+0.79%** | −0.31% | 1.1 | 1.4 |
| Jul 4 | **+0.68%** | +0.09% | **+0.28%** | −0.41% | 1.4 | 1.0 |

**Verdict against the decision rule:**
1. best chase edge > passive edge net of fee — **FAILS all 3 days** (chase edge is below passive
   at every day and every fee; at 0.2c fee chase is ~breakeven-to-negative while passive stays
   +0.28…+0.79%).
2. chase adverse < passive adverse — **inconsistent** (lower on Jul 2/4, HIGHER on Jul 3).
3. robustness — chase never beats passive, so moot.

**Conclusion:** the momentum chase reduces adverse residual on some days but pays too much (buys
the winner at its ask) to be worth it — net worse than passive on every day. **Chase is closed.**

**What this DOES establish (the real deliverable):** the PASSIVE gated top-of-book maker is a
robust, backtested **+EV strategy on real book data — +0.68% / +0.90% / +1.19% across 3 days
(avg ~+0.92%)**, staying positive until ~0.4c/share fee. That IS the current gated default
(size 5, cap 6, skip trends, linked-pair). 0xb27b's every-window edge is NOT a chase signal we
were missing — it is execution quality (fill speed/volume, continuous merge at scale) we cannot
replicate maker-side. **Recommendation: run the passive gated default; verify the real maker fee
is < ~0.4c before any live; do NOT pursue chase or aggressive/neutral every-window quoting.**
